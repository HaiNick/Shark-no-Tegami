"""
Shark-no-Tegami - Streamable HTTP transport for claude.ai
Exposes a send_email tool so Claude can send emails via a Postfix relay over Tailscale.
"""

import os
import logging
import tomllib
from email.mime.text import MIMEText
from pathlib import Path

import aiosmtplib
from fastmcp import FastMCP

with open(Path(__file__).resolve().parent.parent / "pyproject.toml", "rb") as f:
    __version__ = tomllib.load(f)["project"]["version"]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("shark-no-tegami")

# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

API_KEY = os.getenv("MCP_API_KEY", "")
OIDC_ENABLED = os.getenv("OIDC_ENABLED", "").lower() in {"1", "true", "yes"}

if OIDC_ENABLED and API_KEY:
    raise RuntimeError(
        "OIDC_ENABLED and MCP_API_KEY cannot both be set. Choose one auth mode."
    )

_OIDC_CONFIG_URL = os.getenv("OIDC_CONFIG_URL", "")
_OIDC_CLIENT_ID = os.getenv("OIDC_CLIENT_ID", "")
_OIDC_CLIENT_SECRET = os.getenv("OIDC_CLIENT_SECRET", "")
_OIDC_BASE_URL = os.getenv("OIDC_BASE_URL", "")
_JWT_SIGNING_KEY = os.getenv("JWT_SIGNING_KEY", "")
_STORAGE_ENCRYPTION_KEY = os.getenv("STORAGE_ENCRYPTION_KEY", "")

if OIDC_ENABLED:
    _missing = [
        name
        for name, val in [
            ("OIDC_CONFIG_URL", _OIDC_CONFIG_URL),
            ("OIDC_CLIENT_ID", _OIDC_CLIENT_ID),
            ("OIDC_BASE_URL", _OIDC_BASE_URL),
            ("JWT_SIGNING_KEY", _JWT_SIGNING_KEY),
            ("STORAGE_ENCRYPTION_KEY", _STORAGE_ENCRYPTION_KEY),
        ]
        if not val
    ]
    if _missing:
        raise RuntimeError(
            f"OIDC_ENABLED=true but missing required vars: {', '.join(_missing)}"
        )

SMTP_HOST = os.getenv("SMTP_HOST", "localhost")
SMTP_PORT = int(os.getenv("SMTP_PORT", "25"))
MAIL_FROM = os.getenv("MAIL_FROM", "")
if not MAIL_FROM:
    raise RuntimeError("MAIL_FROM env var is required")

_INSTRUCTIONS = (
    "Shark-no-Tegami is an email sending tool. Use it to send plain-text or HTML emails via a configured Postfix relay.\n\n"
    "Tool:\n"
    "- send_email: Send an email to one or more recipients.\n\n"
    "Notes:\n"
    "- body is plain text or HTML depending on content_type\n"
    "- content_type: 'plain' (default) or 'html'\n"
    "- to supports comma-separated recipients (e.g. 'a@example.com,b@example.com')\n"
    "- Used for automated digest and alert emails from Claude Code Routines"
)

if OIDC_ENABLED:
    from fastmcp.server.auth.oidc_proxy import OIDCProxy
    from key_value.aio.stores.filetree.store import FileTreeStore
    from key_value.aio.wrappers.encryption.fernet import FernetEncryptionWrapper
    from cryptography.fernet import Fernet

    _client_storage = FernetEncryptionWrapper(
        key_value=FileTreeStore(data_directory="/app/oauth_state"),
        fernet=Fernet(_STORAGE_ENCRYPTION_KEY),
    )
    _auth = OIDCProxy(
        config_url=_OIDC_CONFIG_URL,
        client_id=_OIDC_CLIENT_ID,
        client_secret=_OIDC_CLIENT_SECRET or None,
        base_url=_OIDC_BASE_URL,
        jwt_signing_key=_JWT_SIGNING_KEY,
        required_scopes=["openid"],
        verify_id_token=True,
        client_storage=_client_storage,
    )
    mcp = FastMCP(name="Shark-no-Tegami", instructions=_INSTRUCTIONS, auth=_auth)
else:
    mcp = FastMCP(name="Shark-no-Tegami", instructions=_INSTRUCTIONS)

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def send_email(to: str, subject: str, body: str, content_type: str = "plain") -> str:
    """
    Send an email via the configured Postfix relay.

    Args:
        to: Recipient email address or comma-separated list of addresses.

        subject: Email subject line.

        body: Plain text or HTML email body.

        content_type: Either "plain" (default) or "html".
    """
    recipients = [addr.strip() for addr in to.split(",") if addr.strip()]

    msg = MIMEText(body, content_type, "utf-8")
    msg["From"] = MAIL_FROM
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject

    logger.info(f"send_email: to={recipients} subject={subject!r}")
    try:
        await aiosmtplib.send(
            msg,
            hostname=SMTP_HOST,
            port=SMTP_PORT,
            use_tls=False,
            start_tls=False,
        )
        return f"Email sent successfully to {', '.join(recipients)}"
    except Exception as e:
        logger.error(f"send_email failed: {e}")
        return f"Failed to send email: {e}"


# ---------------------------------------------------------------------------
# Auth middleware (optional bearer token check)
# ---------------------------------------------------------------------------

if API_KEY:
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    class BearerAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if request.url.path == "/.well-known/oauth-authorization-server":
                return await call_next(request)
            auth = request.headers.get("authorization", "")
            if auth != f"Bearer {API_KEY}":
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return await call_next(request)

app = mcp.http_app(stateless_http=True, json_response=True)
if API_KEY:
    app.add_middleware(BearerAuthMiddleware)

# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    logger.info(f"Starting Shark-no-Tegami v{__version__} on {host}:{port}")
    if OIDC_ENABLED:
        logger.info("OIDC authentication is ENABLED (Pocket ID)")
    elif API_KEY:
        logger.info("Bearer token auth is ENABLED")
    else:
        logger.warning("No MCP_API_KEY set - server is unauthenticated!")
    uvicorn.run(app, host=host, port=port)
