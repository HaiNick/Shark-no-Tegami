"""Parser-based active-content validator for outgoing HTML."""

import re
from selectolax.lexbor import LexborHTMLParser

BLOCKED_TAGS = {
    "script", "iframe", "frame", "frameset", "object", "embed", "applet",
    "form", "input", "button", "textarea", "select", "base", "link", "meta",
    "svg", "math", "template", "style",
}
URL_ATTRS = {"href", "src", "action", "formaction", "background", "poster", "xlink:href"}
SAFE_SCHEMES = ("https:", "http:", "mailto:")
STYLE_BAD = re.compile(
    r"expression\s*\(|javascript:|vbscript:|url\s*\(|@import|behavior\s*:", re.I
)


def find_violations(html: str, trusted_template: bool = False) -> list[str]:
    out = []
    if not trusted_template and re.search(r"<!--\s*\[if", html, re.I):
        out.append("conditional comment (<!--[if ...]>)")
    tree = LexborHTMLParser(html)
    for node in tree.root.traverse(include_text=False):
        tag = node.tag
        if tag.startswith("-"):
            # Comment and other non-element nodes have no attributes, and reading
            # .attributes on them segfaults selectolax 0.3.x depending on heap state.
            continue
        if tag in BLOCKED_TAGS:
            if trusted_template and tag == "meta" and "http-equiv" not in (node.attributes or {}):
                continue  # the theme's own charset/viewport meta tags
            if trusted_template and tag == "style":
                continue  # the theme's own stylesheet
            out.append(f"<{tag}> element")
            continue
        for name, val in (node.attributes or {}).items():
            n = name.lower()
            v = (val or "").strip()
            if n.startswith("on"):
                out.append(f"{n} attribute on <{tag}>")
            elif n in URL_ATTRS and v:
                low = re.sub(r"[\x00-\x20]", "", v).lower()
                if ":" in low.split("/")[0] and not low.startswith(SAFE_SCHEMES):
                    out.append(f"{n}={v[:40]!r} on <{tag}> (scheme not allowed)")
            elif n == "style" and STYLE_BAD.search(v):
                out.append(f"style on <{tag}> contains blocked CSS")
            elif n in ("srcdoc", "http-equiv"):
                out.append(f"{n} attribute on <{tag}>")
    return out
