"""send_email pipeline: validate, resolve, render, send. Never logs bodies."""

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

import aiosmtplib
import httpx

from .config import Config
from .errors import TegamiError
from .refs import Body, fetch_ref, parse_ref
from .render import CONTENT_TYPES, THEMED, Rendered, render
from .themes import INPUTS, NAME_RE, Theme, ThemeStore

logger = logging.getLogger("shark-no-tegami")

MAX_SUBJECT = 200
PREVIEW_CHARS = 500
_ADDR_RE = re.compile(r"^[^@\s<>,;\"]+@[^@\s<>,;\"]+$")


@dataclass(frozen=True)
class Relay:
    mail_from: str
    host: str
    port: int


@dataclass(frozen=True)
class Request:
    to: str
    subject: str
    body: str | None = None
    body_ref: str | None = None
    content_type: str = "plain"
    theme: str | None = None
    cc: str = ""
    bcc: str = ""
    dry_run: bool = False


def split_addresses(value: str, field: str) -> list[str]:
    addrs = [a.strip() for a in (value or "").split(",") if a.strip()]
    for addr in addrs:
        if not _ADDR_RE.match(addr):
            raise TegamiError("E_ARGS", f"{field} contains an invalid address")
    return addrs


def allowed_for(cfg: Config, theme: Theme | None) -> tuple[frozenset[str] | None, str]:
    """Effective allowlist: the global list, narrowed (never widened) by the theme's own."""
    narrow = theme.allowed_recipients if theme else None
    if narrow is None:
        return cfg.allowed_recipients, "TEGAMI_ALLOWED_RECIPIENTS"
    if cfg.allowed_recipients is None:
        return narrow, f"theme {theme.name!r} allowed_recipients"
    return cfg.allowed_recipients & narrow, f"TEGAMI_ALLOWED_RECIPIENTS and theme {theme.name!r} allowed_recipients"


def check_recipients(fields: dict[str, list[str]], cfg: Config, theme: Theme | None) -> None:
    allowed, source = allowed_for(cfg, theme)
    if allowed is None:
        return
    for field, addrs in fields.items():
        blocked = [a for a in addrs if a.lower() not in allowed]
        if blocked:
            raise TegamiError("E_RECIPIENT", f"{len(blocked)} {field} address(es) not allowed by {source}", blocked)


def check_args(req: Request) -> tuple[list[str], list[str], list[str], str | None]:
    """Checks that need no filesystem access. Returns to, cc, bcc, and the theme name."""
    if bool(req.body) == bool(req.body_ref):
        raise TegamiError("E_ARGS", "pass exactly one of body and body_ref")
    if req.content_type not in CONTENT_TYPES:
        raise TegamiError("E_ARGS", f"content_type must be one of {', '.join(CONTENT_TYPES)}")
    theme = req.theme or None
    if theme is not None and req.content_type not in THEMED:
        raise TegamiError("E_ARGS", "theme is only valid with content_type 'markdown' or 'json'")
    if req.content_type == "markdown":
        theme = theme or "default"
    if req.content_type == "json" and theme is None:
        raise TegamiError("E_ARGS", "content_type 'json' needs a theme with input: json")
    if theme is not None and not NAME_RE.match(theme):
        raise TegamiError("E_ARGS", "theme must match ^[a-z0-9-]{1,32}$")
    if "\r" in req.subject or "\n" in req.subject:
        raise TegamiError("E_ARGS", "subject must not contain CR or LF")
    if len(req.subject) > MAX_SUBJECT:
        raise TegamiError("E_ARGS", f"subject is longer than {MAX_SUBJECT} characters")

    to = split_addresses(req.to, "to")
    if not to:
        raise TegamiError("E_ARGS", "to must contain at least one address")
    return to, split_addresses(req.cc, "cc"), split_addresses(req.bcc, "bcc"), theme


def resolve_theme(name: str | None, content_type: str, cfg: Config) -> tuple[Theme | None, ThemeStore | None]:
    if name is None:
        return None, None
    store = ThemeStore(cfg.themes_dir, cfg.themes_default_dir)
    theme = store.resolve(name)
    expected = INPUTS[theme.input]
    if content_type != expected:
        raise TegamiError("E_ARGS", f"theme {name!r} has input {theme.input!r} and takes content_type {expected!r}")
    return theme, store


async def resolve_body(req: Request, cfg: Config, transport: httpx.AsyncBaseTransport | None, meta: dict) -> Body:
    if req.body_ref:
        ref = parse_ref(req.body_ref, cfg)
        meta["source"] = str(ref)
        return await fetch_ref(ref, cfg, transport)
    data = req.body.encode("utf-8")
    if len(data) > cfg.max_body_bytes:
        raise TegamiError("E_TOO_LARGE", f"body is {len(data)} bytes, limit is {cfg.max_body_bytes}")
    return Body(req.body, hashlib.sha256(data).hexdigest(), "inline")


def build_message(relay: Relay, to: list[str], cc: list[str], subject: str, rendered: Rendered) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = relay.mail_from
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    # BCC is intentionally not added to headers; it only goes into the envelope.
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=relay.mail_from.rpartition("@")[2] or None)
    msg.set_content(rendered.text, subtype="plain", charset="utf-8")
    if rendered.html is not None:
        msg.add_alternative(rendered.html, subtype="html", charset="utf-8")
    return msg


async def process(
    req: Request,
    cfg: Config,
    relay: Relay,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    sender=None,
) -> dict:
    # source stays a placeholder until parse_ref accepts the ref, so caller text never reaches the log.
    meta = {"rcpt": 0, "source": "body_ref" if req.body_ref else "inline", "sha256": "-", "html_bytes": 0}
    try:
        result = await _process(req, cfg, relay, meta, transport, sender or aiosmtplib.send)
    except TegamiError as e:
        result = e.to_result()
    code = result["code"] if result["status"] == "error" else result["status"]
    logger.info(
        "send_email ts=%s rcpt=%d source=%s sha256=%s html_bytes=%d result=%s",
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        meta["rcpt"], meta["source"], meta["sha256"], meta["html_bytes"], code,
    )
    return result


async def _process(req, cfg, relay, meta, transport, sender) -> dict:
    to, cc, bcc, theme_name = check_args(req)
    meta["rcpt"] = len(to) + len(cc) + len(bcc)
    theme, store = resolve_theme(theme_name, req.content_type, cfg)
    check_recipients({"to": to, "cc": cc, "bcc": bcc}, cfg, theme)

    body = await resolve_body(req, cfg, transport, meta)
    meta["source"], meta["sha256"] = body.source, body.sha256

    rendered = render(body.text, req.content_type, theme, req.subject, store)
    html_bytes = len(rendered.html.encode("utf-8")) if rendered.html is not None else 0
    meta["html_bytes"] = html_bytes

    result = {"status": "validated" if req.dry_run else "sent"}
    if req.dry_run:
        result["html_preview"] = (rendered.html or "")[:PREVIEW_CHARS]
    else:
        msg = build_message(relay, to, cc, req.subject, rendered)
        try:
            await sender(
                msg,
                hostname=relay.host,
                port=relay.port,
                recipients=to + cc + bcc,
                use_tls=False,
                start_tls=False,
            )
        except (aiosmtplib.SMTPException, OSError, TimeoutError) as e:
            raise TegamiError("E_SMTP", f"relay error: {type(e).__name__}: {e}")
        result["message_id"] = msg["Message-ID"]

    result.update({"source": body.source, "source_sha256": body.sha256, "html_bytes": html_bytes})
    if rendered.items is not None:
        result["items"] = rendered.items
    return result
