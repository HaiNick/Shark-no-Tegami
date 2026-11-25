"""Cloudflare Email Service REST transport: send the finished MIME message over HTTPS.

Used when outbound SMTP ports are blocked (Hetzner Cloud blocks 25 and 465).
POST /accounts/{account_id}/email/sending/send_raw takes the full RFC 5322
message plus the SMTP envelope, so Tegami's own EmailMessage goes out as is.
"""

from email.message import EmailMessage
from email.policy import SMTP

import httpx

from .errors import TegamiError

# Fixed, never taken from env or the caller, so the token only ever goes to Cloudflare.
CF_API = "https://api.cloudflare.com/client/v4"


def _errors(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:200]
    errs = data.get("errors") or []
    parts = [f"{e.get('code', '?')}: {e.get('message', '')}" for e in errs if isinstance(e, dict)]
    return "; ".join(parts)[:300] or "no error details"


async def send_raw(
    msg: EmailMessage,
    *,
    mail_from: str,
    recipients: list[str],
    account_id: str,
    api_token: str,
    timeout: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str | None:
    """Send msg through Cloudflare. Returns Cloudflare's message id if it reports one."""
    url = f"{CF_API}/accounts/{account_id}/email/sending/send_raw"
    payload = {
        "from": mail_from,
        "recipients": recipients,
        "mime_message": msg.as_bytes(policy=SMTP).decode("utf-8"),
    }
    headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(transport=transport, timeout=timeout, follow_redirects=False) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException:
        raise TegamiError("E_SMTP", f"cloudflare api: timed out after {timeout:g} s")
    except httpx.HTTPError as e:
        raise TegamiError("E_SMTP", f"cloudflare api: {type(e).__name__}")

    if resp.status_code >= 300:
        raise TegamiError("E_SMTP", f"cloudflare api: HTTP {resp.status_code}: {_errors(resp)}")
    try:
        data = resp.json()
    except ValueError:
        raise TegamiError("E_SMTP", "cloudflare api: response is not JSON")
    if not data.get("success", False):
        raise TegamiError("E_SMTP", f"cloudflare api: {_errors(resp)}")
    result = data.get("result")
    if isinstance(result, dict):
        for key in ("message_id", "messageId", "id"):
            if isinstance(result.get(key), str):
                return result[key]
    return None
