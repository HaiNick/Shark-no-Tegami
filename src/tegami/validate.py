"""Parser-based active-content validator for outgoing HTML."""

import html as htmllib
import re
from selectolax.lexbor import LexborHTMLParser

BLOCKED_TAGS = {
    "script", "iframe", "frame", "frameset", "object", "embed", "applet",
    "form", "input", "button", "textarea", "select", "base", "link", "meta",
    "svg", "math", "template", "style",
}
URL_ATTRS = {"href", "src", "action", "formaction", "background", "poster", "xlink:href"}
SAFE_SCHEMES = ("https:", "http:", "mailto:")
# Each "<!--[if ...]>" up to the first "-->" (where the comment ends). Found by
# position rather than by parsing comments, so a "<!--" hidden in an attribute
# value can't shift the boundaries and hide a conditional block.
CONDITIONAL = re.compile(r"<!--\s*\[if[^\]]*\]>(.*?)(?:-->|\Z)", re.I | re.S)
STYLE_BAD = re.compile(
    r"expression\s*\(|javascript:|vbscript:|url\s*\(|@import|behavior\s*:", re.I
)

# Raw token scan of conditional comment bodies. The parser drops markup that is
# invalid in context (a stray <td onclick> outside a table), but Outlook's Word
# engine may still render it, so the raw text is checked as well. <style> and
# <meta> are left to the parsed pass, which applies the template rules to them.
RAW_TAG = re.compile(r"<\s*/?\s*(" + "|".join(sorted(BLOCKED_TAGS - {"style", "meta"})) + r")\b", re.I)
RAW_ATTR = re.compile(r"(?<![\w-])(on[a-z]+|srcdoc|http-equiv)\s*=", re.I)
RAW_SCHEME = re.compile(r"(javascript|vbscript|data):", re.I)


def _raw_scan(body: str) -> list[str]:
    out = [f"<{m.group(1).lower()}> tag (raw scan)" for m in RAW_TAG.finditer(body)]
    out += [f"{m.group(1).lower()} attribute (raw scan)" for m in RAW_ATTR.finditer(body)]
    squashed = re.sub(r"[\x00-\x20]", "", htmllib.unescape(body))
    out += [f"{m.group(1).lower()}: scheme (raw scan)" for m in RAW_SCHEME.finditer(squashed)]
    return out


def find_violations(html: str, trusted_template: bool = False) -> list[str]:
    out = []
    if not trusted_template and re.search(r"<!--\s*\[if", html, re.I):
        out.append("conditional comment (<!--[if ...]>)")
    if trusted_template:
        # Outlook renders conditional blocks, but the parser sees only a comment,
        # so their content is validated as HTML in its own right.
        # Parsed twice: a stray <td> or <tr> is dropped outside a table, so the
        # wrapped pass keeps table parts that the plain pass would lose.
        for m in CONDITIONAL.finditer(html):
            inner = m.group(1)
            found = find_violations(inner, trusted_template=True)
            found += find_violations(f"<table>{inner}</table>", trusted_template=True)
            found += _raw_scan(inner)
            out.extend(f"{v} (inside conditional comment)" for v in dict.fromkeys(found))
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
                # The theme's own stylesheet, held to the same CSS rules as inline styles.
                if STYLE_BAD.search(node.text(deep=True) or ""):
                    out.append("<style> element contains blocked CSS")
                continue
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
