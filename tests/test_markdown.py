from tegami.pipeline import Request, process
from conftest import render_theme as render
from tegami.validate import find_violations


def test_javascript_link_is_not_a_link():
    out = render("[x](javascript:alert(1))", "markdown", "default", "t").html
    assert "<a" not in out
    assert "javascript:alert(1)" in out  # left as literal text


def test_raw_script_is_escaped():
    out = render("hi <script>alert(1)</script>", "markdown", "default", "t").html
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_default_theme_output_is_inlined_and_clean():
    out = render("# Title\n\n[ok](https://example.com)", "markdown", "default", "t").html
    assert "<style" not in out  # css_inline drops the template's <style> after inlining
    assert "max-width: 640px" in out
    assert '<a href="https://example.com"' in out
    assert find_violations(out) == []


async def test_markdown_text_part_is_the_source(cfg, relay, sender):
    src = "# Title\n\nBody text\n"
    r = await process(Request(to="01nicklas07@gmail.com", subject="s", body=src, content_type="markdown"), cfg, relay, sender=sender)
    assert r["status"] == "sent" and "items" not in r
    msg = sender.calls[0][0]
    plain, html = msg.get_payload()
    assert plain.get_content_type() == "text/plain" and plain.get_content() == src
    assert html.get_content_type() == "text/html"
