import pytest

from tegami.pipeline import Relay, Request, process, relay_from_env

OK = "01nicklas07@gmail.com"
CF = {
    "MAIL_FROM": "tegami@snowy-burbot.com",
    "SMTP_HOST": "smtp.mx.cloudflare.net",
    "SMTP_TLS": "implicit",
    "SMTP_USERNAME": "api_token",
    "SMTP_PASSWORD": "cf-secret-token",
}


def test_defaults_are_plain_postfix():
    r = relay_from_env({"MAIL_FROM": "tegami@snowy-burbot.com"})
    assert (r.host, r.port, r.tls, r.username, r.password) == ("localhost", 25, "none", None, None)
    kw = r.send_kwargs()
    assert kw["use_tls"] is False and kw["start_tls"] is False
    assert "username" not in kw and "password" not in kw


def test_cloudflare_implicit_tls_defaults_to_465():
    r = relay_from_env(CF)
    assert (r.host, r.port, r.tls) == ("smtp.mx.cloudflare.net", 465, "implicit")
    kw = r.send_kwargs()
    assert kw["use_tls"] is True and kw["start_tls"] is False
    assert kw["username"] == "api_token" and kw["password"] == "cf-secret-token"


def test_explicit_port_wins():
    assert relay_from_env({**CF, "SMTP_PORT": "2465"}).port == 2465


def test_starttls_mode():
    kw = relay_from_env({**CF, "SMTP_TLS": "starttls", "SMTP_PORT": "587"}).send_kwargs()
    assert kw["use_tls"] is False and kw["start_tls"] is True


def test_password_not_in_repr():
    assert "cf-secret-token" not in repr(relay_from_env(CF))


@pytest.mark.parametrize(
    "env, msg",
    [
        ({}, "MAIL_FROM"),
        ({**CF, "SMTP_TLS": "ssl"}, "SMTP_TLS"),
        ({**CF, "SMTP_PASSWORD": ""}, "both"),
        ({**CF, "SMTP_USERNAME": ""}, "both"),
        ({**CF, "SMTP_TLS": "none"}, "plain text"),
    ],
)
def test_bad_config_fails_at_startup(env, msg):
    with pytest.raises(RuntimeError, match=msg):
        relay_from_env(env)


async def test_send_passes_tls_and_auth(cfg, sender):
    relay = relay_from_env(CF)
    r = await process(Request(to=OK, subject="s", body="hi"), cfg, relay, sender=sender)
    assert r["status"] == "sent"
    msg, kw = sender.calls[0]
    assert msg["From"] == "tegami@snowy-burbot.com"
    assert r["message_id"].endswith("@snowy-burbot.com>")
    assert kw["hostname"] == "smtp.mx.cloudflare.net" and kw["port"] == 465
    assert kw["use_tls"] is True and kw["username"] == "api_token"
    assert kw["recipients"] == [OK]


async def test_smtp_error_does_not_leak_password(cfg):
    import aiosmtplib

    from conftest import SpySender

    spy = SpySender(exc=aiosmtplib.SMTPAuthenticationError(535, "5.7.8 Authentication failed"))
    r = await process(Request(to=OK, subject="s", body="x"), cfg, relay_from_env(CF), sender=spy)
    assert r["code"] == "E_SMTP" and "535" in r["message"]
    assert "cf-secret-token" not in str(r)


def test_relay_dataclass_still_accepts_positional_basics():
    assert Relay("a@b.c", "h", 25).tls == "none"
