import re

from tegami.pipeline import Request, process
from tegami.render import render
from tegami.validate import find_violations


def req(body, **kw):
    return Request(to="01nicklas07@gmail.com", subject="Shimbun 2026-W44", body=body, content_type="markdown", theme="shimbun", **kw)


async def test_valid_digest_renders_with_counts(cfg, relay, sender, digest_md):
    r = await process(req(digest_md, dry_run=True), cfg, relay, sender=sender)
    assert r["status"] == "validated", r
    assert r["items"] == {"Security": 1, "Releases": 1}
    assert r["html_bytes"] > 0 and r["html_preview"].startswith("<!DOCTYPE html>")


def test_rendered_html_passes_trusted_validator(digest_md):
    out = render(digest_md, "markdown", "shimbun", "t").html
    assert find_violations(out, trusted_template=True) == []
    assert "Node2Shell" in out


async def test_unknown_badge_reports_line(cfg, relay, sender, digest_md):
    bad = digest_md.replace("`actively exploited`", "`totally safe`")
    line = bad.splitlines().index("`totally safe`") + 1
    r = await process(req(bad), cfg, relay, sender=sender)
    assert r["code"] == "E_THEME_PARSE"
    assert r["message"].startswith(f"line {line}:") and "unknown badge" in r["message"]
    assert sender.calls == []


async def test_front_matter_yaml_error_is_theme_parse(cfg, relay, sender, digest_md):
    bad = digest_md.replace('title: "Shark-no-Shimbun"', 'title: [unclosed')
    r = await process(req(bad), cfg, relay, sender=sender)
    assert r["code"] == "E_THEME_PARSE"
    assert re.match(r"line \d+: front matter is not valid YAML", r["message"]), r["message"]


async def test_non_https_item_url_is_rejected(cfg, relay, sender, digest_md):
    bad = digest_md.replace("(https://github.com/tailscale", "(javascript://github.com/tailscale")
    r = await process(req(bad), cfg, relay, sender=sender)
    assert r["code"] == "E_THEME_PARSE"
