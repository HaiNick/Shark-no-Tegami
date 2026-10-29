import logging

import aiosmtplib

from conftest import SpySender
from tegami.pipeline import Request, process

OK = "01nicklas07@gmail.com"


async def test_backcompat_html_body_sends_multipart(cfg, relay, sender):
    html = '<p>Hello <a href="https://example.com">there</a></p>'
    r = await process(Request(to=OK, subject="s", body=html, content_type="html", cc="nicklas@snowy-burbot.com"), cfg, relay, sender=sender)
    assert r["status"] == "sent" and r["message_id"].startswith("<")
    msg, kw = sender.calls[0]
    assert msg.get_content_type() == "multipart/alternative"
    plain, part = msg.get_payload()
    assert plain.get_content().strip() == "Hello there"
    assert part.get_content().strip() == html
    assert msg["Message-ID"] == r["message_id"] and msg["Date"]
    assert kw["recipients"] == [OK, "nicklas@snowy-burbot.com"]
    assert kw["use_tls"] is False and kw["start_tls"] is False


async def test_backcompat_plain_is_text_only(cfg, relay, sender):
    r = await process(Request(to=OK, subject="s", body="hi", bcc="nicklas@snowy-burbot.com"), cfg, relay, sender=sender)
    assert r["status"] == "sent" and r["html_bytes"] == 0
    msg, kw = sender.calls[0]
    assert msg.get_content_type() == "text/plain"
    assert "Bcc" not in msg and "nicklas@snowy-burbot.com" in kw["recipients"]


async def test_unsafe_html_is_rejected(cfg, relay, sender):
    r = await process(Request(to=OK, subject="s", body="<img src=x onerror=alert(1)><script>x</script>", content_type="html"), cfg, relay, sender=sender)
    assert r == {"status": "error", "code": "E_UNSAFE_CONTENT", "message": "2 violations", "details": r["details"]}
    assert len(r["details"]) == 2
    assert sender.calls == []


async def test_dry_run_never_calls_smtp(cfg, relay, sender):
    for ct in ("plain", "html", "markdown"):
        r = await process(Request(to=OK, subject="s", body="<p>x</p>", content_type=ct, dry_run=True), cfg, relay, sender=sender)
        assert r["status"] == "validated" and "message_id" not in r
    assert sender.calls == []


async def test_smtp_failure(cfg, relay):
    spy = SpySender(exc=aiosmtplib.SMTPConnectError("refused"))
    r = await process(Request(to=OK, subject="s", body="x"), cfg, relay, sender=spy)
    assert r["code"] == "E_SMTP"


async def test_log_has_metadata_not_body(cfg, relay, sender, caplog):
    secret = "BODY-SENTINEL-4711"
    with caplog.at_level(logging.INFO, logger="shark-no-tegami"):
        await process(Request(to=OK, subject="SUBJECT-SENTINEL", body=secret), cfg, relay, sender=sender)
        await process(Request(to=OK, subject="s", body=f"<script>{secret}</script>", content_type="html"), cfg, relay, sender=sender)
    lines = [r.getMessage() for r in caplog.records]
    assert len(lines) == 2
    assert all(secret not in line and "SUBJECT-SENTINEL" not in line for line in lines)
    assert "rcpt=1 source=inline" in lines[0] and lines[0].endswith("result=sent")
    assert lines[1].endswith("result=E_UNSAFE_CONTENT")


async def test_invalid_ref_is_not_logged(cfg, relay, sender, caplog):
    with caplog.at_level(logging.INFO, logger="shark-no-tegami"):
        await process(Request(to=OK, subject="s", body_ref="x\nFAKE LOG LINE"), cfg, relay, sender=sender)
    assert "FAKE LOG LINE" not in caplog.text and "source=body_ref" in caplog.text
