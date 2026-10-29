"""Turn a resolved body into the text and HTML parts of the message."""

from dataclasses import dataclass

from selectolax.lexbor import LexborHTMLParser

from .errors import TegamiError
from .themes import Theme, ThemeStore
from .validate import find_violations

CONTENT_TYPES = ("plain", "html", "markdown", "json")
THEMED = ("markdown", "json")


@dataclass(frozen=True)
class Rendered:
    text: str
    html: str | None
    items: dict[str, int] | None = None


def _require_clean(html: str) -> None:
    violations = find_violations(html)
    if violations:
        noun = "violation" if len(violations) == 1 else "violations"
        raise TegamiError("E_UNSAFE_CONTENT", f"{len(violations)} {noun}", violations)


_BLOCKS = "p,div,br,li,tr,h1,h2,h3,h4,h5,h6,blockquote,pre,table,hr"


def html_to_text(html: str) -> str:
    """Tag-stripped fallback for the text/plain part. Breaks lines at block elements only."""
    tree = LexborHTMLParser(html)
    tree.strip_tags(["style", "script", "title", "head"])
    for node in tree.css(_BLOCKS):
        node.insert_after("\n")
    lines = (" ".join(line.split()) for line in tree.text(separator="").splitlines())
    return "\n".join(line for line in lines if line)


def render(source: str, content_type: str, theme: Theme | None, subject: str, store: ThemeStore | None) -> Rendered:
    if content_type == "plain":
        return Rendered(text=source, html=None)
    if content_type == "html":
        _require_clean(source)
        return Rendered(text=html_to_text(source), html=source)
    out = store.render(theme, source, subject)
    return Rendered(text=out.text if out.text is not None else html_to_text(out.html), html=out.html, items=out.items)
