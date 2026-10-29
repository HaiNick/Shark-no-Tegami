"""
Shark-no-Tegami - Streamable HTTP transport for claude.ai
Exposes a send_email tool so Claude can send emails via a Postfix relay over Tailscale.
"""

import os
import logging
import sys
import tomllib
from pathlib import Path

from fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tegami.config import load_config  # noqa: E402
from tegami.pipeline import Relay, Request, process  # noqa: E402

with open(Path(__file__).resolve().parent.parent / "pyproject.toml", "rb") as f:
    __version__ = tomllib.load(f)["project"]["version"]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("shark-no-tegami")
# httpx logs request URLs at INFO; Tegami writes its own metadata line instead.
logging.getLogger("httpx").setLevel(logging.WARNING)

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
if OIDC_ENABLED:
    _missing = [
        name
        for name, val in [
            ("OIDC_CONFIG_URL", _OIDC_CONFIG_URL),
            ("OIDC_CLIENT_ID", _OIDC_CLIENT_ID),
            ("OIDC_BASE_URL", _OIDC_BASE_URL),
            ("JWT_SIGNING_KEY", _JWT_SIGNING_KEY),
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

TEGAMI = load_config()
RELAY = Relay(mail_from=MAIL_FROM, host=SMTP_HOST, port=SMTP_PORT)

_INSTRUCTIONS = (
    "Shark-no-Tegami is an email sending tool. Use it to send plain-text, HTML, or Markdown emails via a configured Postfix relay.\n\n"
    "Tool:\n"
    "- send_email: Send an email to one or more recipients. Returns a JSON object.\n\n"
    "Body:\n"
    "- Pass exactly one of body (inline text) or body_ref ('OWNER/REPO@SHA40:PATH', a file at a full 40-char commit SHA in an allowlisted repo and path prefix; .md, .html, or .txt)\n"
    "- content_type: 'plain' (default), 'html', or 'markdown'\n"
    "- theme (markdown only): 'default' (default) or 'shimbun' (strict Shark-no-Shimbun digest dialect; result includes per-section 'items' counts)\n"
    "- HTML bodies and all rendered output are checked for active content (scripts, event handlers, non-http(s)/mailto URLs, blocked CSS, forms, embeds) and rejected if any is found\n"
    "- dry_run: true resolves, validates, and renders without sending; status is 'validated' and html_preview holds the first 500 characters\n\n"
    "Recipients and subject:\n"
    "- to, cc, bcc: comma-separated addresses (e.g. 'a@example.com,b@example.com'); all must be on the server's recipient allowlist when one is configured\n"
    "- subject: max 200 characters, no CR or LF\n\n"
    "Results:\n"
    "- Success: {status: 'sent' | 'validated', message_id, source, source_sha256, html_bytes, items?, html_preview?}\n"
    "- Error: {status: 'error', code, message, details?}. Branch on code:\n"
    "  E_ARGS (bad or conflicting arguments), E_RECIPIENT (address not allowlisted), E_REF_FORMAT (malformed body_ref),\n"
    "  E_REF_NOT_ALLOWED (repo or path not allowlisted), E_REF_FETCH (GitHub error, timeout, or non-UTF-8 content),\n"
    "  E_TOO_LARGE (body over the byte limit), E_UNSAFE_CONTENT (details lists violations),\n"
    "  E_THEME_PARSE (digest does not match the Shimbun dialect; message starts with 'line N:'), E_SMTP (relay refused or timed out)\n\n"
    "- Used for automated digest and alert emails from Claude Code Routines"
)

if OIDC_ENABLED:
    from fastmcp.server.auth.oidc_proxy import OIDCProxy

    # 2026-07-13: Removed FileTreeStore-based client_storage (and py-key-value-aio /
    # cryptography deps + STORAGE_ENCRYPTION_KEY env var).  Two reasons drove this:
    #   1. Pocket ID 2.10 (fosite migration) rejects the RFC 8707 `resource` parameter
    #      that OIDCProxy forwards upstream → fixed by forward_resource=False.
    #   2. Claude.ai registers via CIMD (client_id is a URL).  FileTreeStore uses the
    #      client_id as a filesystem path component, which crashes on URL-shaped keys
    #      with a 500 on /authorize.  Fleet-wide decision: use FastMCP's default
    #      in-memory client storage instead.  Trade-off: container restart requires
    #      re-authentication (acceptable for this workload).
    _auth = OIDCProxy(
        config_url=_OIDC_CONFIG_URL,
        client_id=_OIDC_CLIENT_ID,
        client_secret=_OIDC_CLIENT_SECRET or None,
        base_url=_OIDC_BASE_URL,
        jwt_signing_key=_JWT_SIGNING_KEY,
        required_scopes=["openid"],
        verify_id_token=True,
        forward_resource=False,
    )
    mcp = FastMCP(name="Shark-no-Tegami", instructions=_INSTRUCTIONS, auth=_auth)
else:
    mcp = FastMCP(name="Shark-no-Tegami", instructions=_INSTRUCTIONS)

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def send_email(
    to: str,
    subject: str,
    body: str | None = None,
    content_type: str = "plain",
    cc: str = "",
    bcc: str = "",
    body_ref: str | None = None,
    theme: str | None = None,
    dry_run: bool = False,
) -> dict:
    """
    Send an email via the configured Postfix relay. Returns a JSON result; errors
    come back as {"status": "error", "code": ...} rather than exceptions.

    Args:
        to: Recipient address or comma-separated list. Must be allowlisted if the server has an allowlist.
        subject: Subject line. Max 200 characters, no CR or LF.
        body: Inline body. Pass exactly one of body and body_ref.
        content_type: "plain" (default), "html", or "markdown". HTML is checked for active content.
        cc: Optional CC addresses, comma-separated.
        bcc: Optional BCC addresses, comma-separated. Never added to headers.
        body_ref: "OWNER/REPO@SHA40:PATH" to fetch the body from an allowlisted GitHub repo and path prefix.
        theme: Only with markdown: "default" (default) or "shimbun".
        dry_run: Resolve, validate, and render, then return stats and an HTML preview without sending.

    Error codes: E_ARGS, E_RECIPIENT, E_REF_FORMAT, E_REF_NOT_ALLOWED, E_REF_FETCH,
    E_TOO_LARGE, E_UNSAFE_CONTENT, E_THEME_PARSE, E_SMTP.
    """
    req = Request(
        to=to,
        subject=subject,
        body=body,
        body_ref=body_ref,
        content_type=content_type,
        theme=theme,
        cc=cc,
        bcc=bcc,
        dry_run=dry_run,
    )
    return await process(req, TEGAMI, RELAY)


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
    uvicorn.run(app, host=host, port=port, proxy_headers=True, forwarded_allow_ips="*")
