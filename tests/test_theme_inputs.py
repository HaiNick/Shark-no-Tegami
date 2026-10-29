"""Theme inputs (markdown, json), theme recipient narrowing, and content_type matching."""

import json
from dataclasses import replace

import pytest

from conftest import SHA, write
from tegami.pipeline import Request, process
from tegami.refs import parse_ref

OK = "01nicklas07@gmail.com"
OTHER = "nicklas@snowy-burbot.com"

ALERT = {
    "title": "Disk almost full on sharky",
    "severity": "warning",
    "summary": "/var is at 91 %.",
    "fields": [{"label": "Host", "value": "sharky"}, {"label": "Used", "value": 91}],
    "link": "https://grafana.example.com/d/disk",
}


async def run(tcfg, relay, sender, **kw):
    kw.setdefault("to", OK)
    kw.setdefault("subject", "s")
    return await process(Request(**kw), tcfg, relay, sender=sender)


# -- markdown input -----------------------------------------------------------

NOTE_TEMPLATE = """<html><body>
<p id="author">{{ meta.author }}</p>
<div id="intro">{{ intro }}</div>
{% for s in sections %}<section data-n="{{ loop.index }}"><h2>{{ s.title }}</h2>{{ s.html }}</section>
{% endfor %}<div id="all">{{ content }}</div>
</body></html>"""

NOTE_BODY = """---
author: Shark <ops>
tags: [a, b]
---
Intro paragraph with <script>alert(1)</script> raw HTML.

## First *part*

One.

## Second

Two <img src=x onerror=alert(1)>.

### Not a split

Still second.
"""


@pytest.fixture
def note_theme(tcfg):
    write(tcfg.themes_dir / "note" / "theme.yaml", "input: markdown\n")
    write(tcfg.themes_dir / "note" / "email.html.j2", NOTE_TEMPLATE)
    return tcfg


async def test_markdown_theme_meta_sections_and_escaping(note_theme, relay, sender):
    r = await run(note_theme, relay, sender, body=NOTE_BODY, content_type="markdown", theme="note")
    assert r["status"] == "sent", r
    html = sender.calls[0][0].get_payload()[1].get_content()
    assert '<p id="author">Shark &lt;ops&gt;</p>' in html
    titles = [line for line in html.splitlines() if line.startswith("<section")]
    assert len(titles) == 2
    assert '<h2>First part</h2><p>One.</p>' in titles[0]
    assert "<h2>Second</h2>" in titles[1] and "Still second." in html.split('data-n="2"')[1]
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<img" not in html and "&lt;img" in html
    assert "Intro paragraph" in html.split('id="intro"')[1].split("</div>")[0]


async def test_markdown_theme_text_part_is_source(note_theme, relay, sender):
    await run(note_theme, relay, sender, body=NOTE_BODY, content_type="markdown", theme="note")
    assert sender.calls[0][0].get_payload()[0].get_content() == NOTE_BODY


async def test_markdown_without_front_matter_has_empty_meta(note_theme, relay, sender):
    write(note_theme.themes_dir / "note" / "email.html.j2", "<p>{{ meta | length }}</p>{{ content }}")
    r = await run(note_theme, relay, sender, body="Just text\n\n---\n\nafter a rule", content_type="markdown", theme="note", dry_run=True)
    assert r["status"] == "validated" and "<p>0</p>" in r["html_preview"]


@pytest.mark.parametrize(
    "body,needle",
    [("---\nauthor: [unclosed\n---\nx", "front matter is not valid YAML"), ("---\n- a\n- b\n---\nx", "front matter must be a mapping")],
)
async def test_markdown_front_matter_errors(note_theme, relay, sender, body, needle):
    r = await run(note_theme, relay, sender, body=body, content_type="markdown", theme="note", dry_run=True)
    assert r["code"] == "E_THEME_PARSE" and r["message"].startswith("line ") and needle in r["message"], r


# -- json input ---------------------------------------------------------------


async def test_alert_renders_with_text_template(tcfg, relay, sender):
    r = await run(tcfg, relay, sender, body=json.dumps(ALERT), content_type="json", theme="alert")
    assert r["status"] == "sent" and "items" not in r, r
    plain, html = sender.calls[0][0].get_payload()
    html = html.get_content()
    assert "#b26a00" in html and "Disk almost full on sharky" in html
    assert '<a href="https://grafana.example.com/d/disk"' in html
    text = plain.get_content()
    assert text.startswith("[WARNING] Disk almost full on sharky")
    assert "Host: sharky" in text and "Used: 91" in text and text.rstrip().endswith("https://grafana.example.com/d/disk")


async def test_alert_without_link_omits_button(tcfg, relay, sender):
    data = {k: v for k, v in ALERT.items() if k != "link"}
    r = await run(tcfg, relay, sender, body=json.dumps(data), content_type="json", theme="alert", dry_run=True)
    assert r["status"] == "validated", r


@pytest.mark.parametrize(
    "mutate,path",
    [
        (lambda d: d.pop("summary"), "$:"),
        (lambda d: d["fields"][1].pop("value"), "$.fields[1]:"),
        (lambda d: d.update(severity="apocalyptic"), "$.severity:"),
        (lambda d: d.update(link="javascript:alert(1)"), "$.link:"),
        (lambda d: d.update(extra=1), "$:"),
    ],
)
async def test_alert_schema_violations(tcfg, relay, sender, mutate, path):
    data = json.loads(json.dumps(ALERT))
    mutate(data)
    r = await run(tcfg, relay, sender, body=json.dumps(data), content_type="json", theme="alert", dry_run=True)
    assert r["code"] == "E_DATA_INVALID" and r["message"].startswith(path), r
    assert sender.calls == []


async def test_alert_string_with_script_is_escaped(tcfg, relay, sender):
    data = dict(ALERT, title="<script>alert(1)</script>", fields=[{"label": "x", "value": '"><img src=x onerror=alert(1)>'}])
    r = await run(tcfg, relay, sender, body=json.dumps(data), content_type="json", theme="alert")
    assert r["status"] == "sent", r
    html = sender.calls[0][0].get_payload()[1].get_content()
    assert "<script>" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<img" not in html


@pytest.mark.parametrize(
    "body,needle",
    [("[1, 2]", "must be a JSON object"), ('{"a": NaN}', "NaN is not allowed"), ("{nope", "invalid JSON at line 1")],
)
async def test_json_body_must_be_an_object(tcfg, relay, sender, body, needle):
    r = await run(tcfg, relay, sender, body=body, content_type="json", theme="alert", dry_run=True)
    assert r["code"] == "E_DATA_INVALID" and needle in r["message"], r


def test_body_ref_accepts_json(cfg):
    assert parse_ref(f"HaiNick/shimbun-archive@{SHA}:digests/alert.json", cfg).path == "digests/alert.json"


# -- allowed_recipients -------------------------------------------------------


def restrict(tcfg, theme, addrs):
    path = tcfg.themes_dir / theme / "theme.yaml"
    base = (tcfg.themes_default_dir / theme / "theme.yaml").read_text()
    write(path, base + "\nallowed_recipients:\n" + "".join(f"  - {a}\n" for a in addrs))


async def test_theme_allowlist_narrows(tcfg, relay, sender):
    restrict(tcfg, "alert", [OK])
    r = await run(tcfg, relay, sender, to=OTHER, body=json.dumps(ALERT), content_type="json", theme="alert", dry_run=True)
    assert r["code"] == "E_RECIPIENT" and "theme 'alert'" in r["message"] and r["details"] == [OTHER]
    r = await run(tcfg, relay, sender, to=OK, body=json.dumps(ALERT), content_type="json", theme="alert", dry_run=True)
    assert r["status"] == "validated"


async def test_theme_allowlist_never_widens(tcfg, relay, sender):
    restrict(tcfg, "alert", [OK, "outsider@example.com"])
    for field in ("to", "cc", "bcc"):
        kw = {"to": OK, field: "outsider@example.com"} if field != "to" else {"to": "outsider@example.com"}
        r = await run(tcfg, relay, sender, body=json.dumps(ALERT), content_type="json", theme="alert", dry_run=True, **kw)
        assert r["code"] == "E_RECIPIENT" and r["details"] == ["outsider@example.com"], (field, r)


async def test_theme_allowlist_applies_without_global_list(tcfg, relay, sender):
    restrict(tcfg, "alert", [OK])
    open_cfg = replace(tcfg, allowed_recipients=None)
    r = await run(open_cfg, relay, sender, to="anyone@example.com", body=json.dumps(ALERT), content_type="json", theme="alert", dry_run=True)
    assert r["code"] == "E_RECIPIENT"


async def test_other_themes_keep_the_global_list(tcfg, relay, sender):
    restrict(tcfg, "alert", [OK])
    r = await run(tcfg, relay, sender, to=OTHER, body="# hi", content_type="markdown", dry_run=True)
    assert r["status"] == "validated"


# -- content_type and theme input must match ----------------------------------


@pytest.mark.parametrize(
    "kw,needle",
    [
        ({"content_type": "json", "theme": "shimbun", "body": "{}"}, "takes content_type 'markdown'"),
        ({"content_type": "markdown", "theme": "alert", "body": "# x"}, "takes content_type 'json'"),
        ({"content_type": "json", "body": "{}"}, "needs a theme with input: json"),
        ({"content_type": "html", "theme": "alert", "body": "<p>x</p>"}, "theme is only valid"),
    ],
)
async def test_content_type_and_input_must_match(tcfg, relay, sender, kw, needle):
    r = await run(tcfg, relay, sender, dry_run=True, **kw)
    assert r["code"] == "E_ARGS" and needle in r["message"], r
