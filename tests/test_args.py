import pytest

from conftest import REF
from tegami.config import Config
from tegami.pipeline import Request, process

OK = "01nicklas07@gmail.com"


async def run(cfg, relay, sender, **kw):
    kw.setdefault("to", OK)
    kw.setdefault("subject", "Hello")
    return await process(Request(**kw), cfg, relay, sender=sender)


@pytest.mark.parametrize(
    "kw",
    [
        {"body": "x", "body_ref": REF},  # both
        {},  # neither
        {"body": "<p>x</p>", "content_type": "html", "theme": "shimbun"},
        {"body": "x", "theme": "default"},  # theme with plain
        {"body": "x", "content_type": "rtf"},
        {"body": "x", "content_type": "markdown", "theme": "fancy"},
        {"body": "x", "subject": "Hi\r\nBcc: evil@x.y"},
        {"body": "x", "subject": "Hi\nthere"},
        {"body": "x", "subject": "a" * 201},
        {"body": "x", "to": ""},
        {"body": "x", "to": "not-an-address"},
    ],
)
async def test_bad_args(cfg, relay, sender, kw):
    r = await run(cfg, relay, sender, **kw)
    assert r["status"] == "error" and r["code"] == "E_ARGS", r
    assert sender.calls == []


@pytest.mark.parametrize("field", ["to", "cc", "bcc"])
async def test_recipient_off_allowlist(cfg, relay, sender, field):
    kw = {"body": "x", field: f"{OK},evil@example.com" if field == "to" else "evil@example.com"}
    r = await run(cfg, relay, sender, **kw)
    assert r["code"] == "E_RECIPIENT"
    assert r["details"] == ["evil@example.com"]
    assert sender.calls == []


async def test_allowlist_is_case_insensitive(cfg, relay, sender):
    r = await run(cfg, relay, sender, body="x", to="01Nicklas07@Gmail.com")
    assert r["status"] == "sent"


async def test_no_allowlist_allows_anyone(relay, sender):
    r = await run(Config(), relay, sender, body="x", to="anyone@example.com")
    assert r["status"] == "sent"


async def test_inline_body_too_large(cfg, relay, sender):
    r = await run(cfg, relay, sender, body="ü" * cfg.max_body_bytes)  # 2 bytes each
    assert r["code"] == "E_TOO_LARGE"
