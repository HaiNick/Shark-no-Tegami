import pytest

from tegami.validate import find_violations

VECTORS = [
    ('<p style="color:red"><a href="https://x.y">ok</a> &lt;script&gt; in a title</p>', []),
    ('<a href="/path">x</a>', []),
    ("<p>a<script>alert(1)</script></p>", ["<script> element"]),
    ("<img src=x onerror=alert(1)>", ["onerror attribute"]),
    ('<a href=" JaVa\tScRiPt:alert(1)">x</a>', ["scheme not allowed"]),
    ('<img src="data:image/svg+xml;base64,AAA">', ["scheme not allowed"]),
    ('<div style="background:url(javascript:x)">x</div>', ["blocked CSS"]),
    ('<svg><a xlink:href="javascript:x"/></svg>', ["<svg> element", "scheme not allowed"]),
    ("<!--[if mso]><v:rect/><![endif]-->", ["conditional comment"]),
    ('<meta http-equiv=refresh content="0;url=https://evil">', ["<meta> element"]),
    ("<style>@import url(https://x)</style><p>x</p>", ["<style> element"]),
]


@pytest.mark.parametrize("html,expected", VECTORS)
def test_vectors(html, expected):
    found = find_violations(html)
    assert len(found) == len(expected), found
    for needle in expected:
        assert any(needle in v for v in found), (needle, found)


def test_trusted_template_relaxes_only_mso_plain_meta_and_style():
    html = '<meta charset="UTF-8"><!--[if mso]><table></table><![endif]--><p>x</p>'
    assert find_violations(html, trusted_template=True) == []
    assert find_violations('<meta http-equiv="refresh" content="0">', trusted_template=True) == ["<meta> element"]
    assert find_violations("<script>x</script>", trusted_template=True) == ["<script> element"]


def test_style_element_blocked_untrusted_allowed_trusted_only_when_clean():
    clean = "<style>p { color: red }</style><p>x</p>"
    assert find_violations(clean) == ["<style> element"]
    assert find_violations(clean, trusted_template=True) == []


@pytest.mark.parametrize(
    "css",
    ["@import url(https://x);", "p { background: url(https://x/t.png) }", "p { width: expression(alert(1)) }", "p { background: javascript:x }"],
)
def test_style_element_with_blocked_css_fails_even_when_trusted(css):
    assert find_violations(f"<style>{css}</style>", trusted_template=True) == ["<style> element contains blocked CSS"]


@pytest.mark.parametrize(
    "html,needle",
    [
        ("<!--[if mso]><script>alert(1)</script><![endif]-->", "<script> element (inside conditional comment)"),
        ('<!--[if mso]><table><tr><td onclick="x()">x</td></tr></table><![endif]-->', "onclick attribute on <td> (inside conditional comment)"),
        ('<!--[if gte mso 9]><td onclick="x()">x</td><![endif]-->', "onclick attribute on <td> (inside conditional comment)"),  # stray <td>
        ('<p title="<!--">x</p><!--[if mso]><script>x</script><![endif]-->', "<script> element (inside conditional comment)"),
        ("<!--[if mso]><script>x</script>", "<script> element (inside conditional comment)"),  # never closed
    ],
)
def test_conditional_comment_content_is_validated_when_trusted(html, needle):
    assert needle in find_violations(html, trusted_template=True)


def test_clean_conditional_comments_pass_when_trusted():
    html = (
        '<!--[if mso]><table><tr><td width="3" bgcolor="#a32d2d">&nbsp;</td></tr></table><![endif]-->'
        '<!--[if !mso]><!--><div style="width:3px;background:#a32d2d">&nbsp;</div><!--<![endif]-->'
    )
    assert find_violations(html, trusted_template=True) == []


def test_conditional_findings_are_not_duplicated():
    found = find_violations("<!--[if mso]><script>x</script><![endif]-->", trusted_template=True)
    assert found == ["<script> element (inside conditional comment)", "<script> tag (raw scan) (inside conditional comment)"]


@pytest.mark.parametrize(
    "body,needle",
    [
        ('<td onclick="x()">x</td>', "onclick attribute (raw scan)"),  # stray <td>, no surrounding table
        ("<v:rect href='vbscript:x'/>", "vbscript: scheme (raw scan)"),
        ('<img src="data:image/png;base64,AAA">', "data: scheme (raw scan)"),
        ("<iframe src=https://x></iframe>", "<iframe> tag (raw scan)"),
        ('<meta http-equiv="refresh" content="0">', "http-equiv attribute (raw scan)"),
    ],
)
def test_conditional_raw_scan(body, needle):
    found = find_violations(f"<!--[if mso]>{body}<![endif]-->", trusted_template=True)
    assert f"{needle} (inside conditional comment)" in found, found


def test_conditional_mso_style_block_is_allowed():
    html = "<!--[if mso]><style>td { mso-line-height-rule: exactly; }</style><![endif]-->"
    assert find_violations(html, trusted_template=True) == []
