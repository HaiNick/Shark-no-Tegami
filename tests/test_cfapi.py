import json

import httpx
import pytest

from tegami.pipeline import Request, process, relay_from_env

OK = "01nicklas07@gmail.com"
ACCOUNT = "c37a5659252adf00ec4e84bac69be196"
TOKEN = "cf-secret-token"
ENV = {
    "MAIL_FROM": "tegami@snowy-burbot.com",
    "TEGAMI_TRANSPORT": "cloudflare_api",
    "TEGAMI_CF_ACCOUNT_ID": ACCOUNT,
    "TEGAMI_CF_API_TOKEN": TOKEN,
}


def mock(status=200, body=None, seen=None, exc=None):
    def handler(request: httpx.Request):
        if exc:
            raise exc
        if seen is not None:
            seen.append(request)
        payload = body if body is not None else {"success": True, "errors": [], "messages": [], "result": {"message_id": "cf-123"}}
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler)


async def send(cfg, sender, tp, **kw):
    req = Request(to=OK, subject="Hello", body="hi", **kw)
    return await process(req, cfg, relay_from_env(ENV), sender=sender, api_transport=tp)


def test_config_cloudflare_api():
    r = relay_from_env(ENV)
    assert r.transport == "cloudflare_api" and r.cf_account_id == ACCOUNT
    assert TOKEN not in repr(r)
    assert r.describe() == f"cloudflare_api account={ACCOUNT} from=tegami@snowy-burbot.com"


def test_default_transport_is_smtp():
    r = relay_from_env({"MAIL_FROM": "a@b.c"})
    assert r.transport == "smtp" and r.describe().startswith("smtp localhost:25 tls=none auth=no")


@pytest.mark.parametrize(
    "env, msg",
    [
        ({**ENV, "TEGAMI_TRANSPORT": "ses"}, "TEGAMI_TRANSPORT"),
        ({**ENV, "TEGAMI_CF_ACCOUNT_ID": ""}, "account ID"),
        ({**ENV, "TEGAMI_CF_ACCOUNT_ID": "not-hex"}, "account ID"),
        ({**ENV, "TEGAMI_CF_API_TOKEN": ""}, "TEGAMI_CF_API_TOKEN"),
    ],
)
def test_bad_cloudflare_config(env, msg):
    with pytest.raises(RuntimeError, match=msg):
        relay_from_env(env)


async def test_send_raw_request(cfg, sender):
    seen = []
    r = await send(cfg, sender, mock(seen=seen), bcc="nicklas@snowy-burbot.com")
    assert r["status"] == "sent" and r["provider_id"] == "cf-123"
    assert r["message_id"].endswith("@snowy-burbot.com>")
    assert sender.calls == []  # SMTP is never touched
    req = seen[0]
    assert req.method == "POST"
    assert str(req.url) == f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/email/sending/send_raw"
    assert req.headers["authorization"] == f"Bearer {TOKEN}"
    data = json.loads(req.content)
    assert data["from"] == "tegami@snowy-burbot.com"
    assert data["recipients"] == [OK, "nicklas@snowy-burbot.com"]
    mime = data["mime_message"]
    assert "From: tegami@snowy-burbot.com\r\n" in mime and "Subject: Hello\r\n" in mime
    assert "Bcc" not in mime and "nicklas@snowy-burbot.com" not in mime
    assert r["message_id"] in mime


async def test_markdown_goes_out_multipart(cfg, sender):
    seen = []
    req = Request(to=OK, subject="s", body="# Title\n\ntext", content_type="markdown")
    r = await process(req, cfg, relay_from_env(ENV), sender=sender, api_transport=mock(seen=seen))
    assert r["status"] == "sent"
    assert "multipart/alternative" in json.loads(seen[0].content)["mime_message"]


async def test_dry_run_never_calls_api(cfg, sender):
    seen = []
    r = await send(cfg, sender, mock(seen=seen), dry_run=True)
    assert r["status"] == "validated" and seen == []


async def test_http_error_is_e_smtp_without_token(cfg, sender):
    body = {"success": False, "errors": [{"code": 10000, "message": "Authentication error"}], "messages": [], "result": None}
    r = await send(cfg, sender, mock(status=403, body=body))
    assert r["code"] == "E_SMTP"
    assert "HTTP 403" in r["message"] and "Authentication error" in r["message"]
    assert TOKEN not in json.dumps(r)


async def test_success_false_is_e_smtp(cfg, sender):
    body = {"success": False, "errors": [{"code": 1, "message": "sender domain not available"}], "result": None}
    r = await send(cfg, sender, mock(status=200, body=body))
    assert r["code"] == "E_SMTP" and "sender domain" in r["message"]


async def test_timeout_is_e_smtp(cfg, sender):
    r = await send(cfg, sender, mock(exc=httpx.ReadTimeout("slow")))
    assert r["code"] == "E_SMTP" and "timed out" in r["message"]


async def test_connect_error_is_e_smtp(cfg, sender):
    r = await send(cfg, sender, mock(exc=httpx.ConnectError("nope")))
    assert r["code"] == "E_SMTP" and "ConnectError" in r["message"]


async def test_success_without_id(cfg, sender):
    r = await send(cfg, sender, mock(body={"success": True, "errors": [], "result": {}}))
    assert r["status"] == "sent" and "provider_id" not in r
