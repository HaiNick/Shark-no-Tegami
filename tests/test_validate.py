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
]


@pytest.mark.parametrize("html,expected", VECTORS)
def test_vectors(html, expected):
    found = find_violations(html)
    assert len(found) == len(expected), found
    for needle in expected:
        assert any(needle in v for v in found), (needle, found)


def test_trusted_template_relaxes_only_mso_and_plain_meta():
    html = '<meta charset="UTF-8"><!--[if mso]><table></table><![endif]--><p>x</p>'
    assert find_violations(html, trusted_template=True) == []
    assert find_violations('<meta http-equiv="refresh" content="0">', trusted_template=True) == ["<meta> element"]
    assert find_violations("<script>x</script>", trusted_template=True) == ["<script> element"]
