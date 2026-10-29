"""Turn a resolved body into the text and HTML parts of the message."""

import html as htmllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import css_inline
import nh3
import yaml
from markdown_it import MarkdownIt
from selectolax.lexbor import LexborHTMLParser

from .errors import TegamiError
from .validate import find_violations
from .vendor.shimbun_lib import DialectError, parse_dialect, render_html

THEMES_DIR = Path(__file__).resolve().parents[2] / "themes"
CONTENT_TYPES = ("plain", "html", "markdown")
THEMES = ("default", "shimbun")

_md = MarkdownIt("commonmark", {"html": False})
_inliner = css_inline.CSSInliner(load_remote_stylesheets=False)

# Trusted wrapper for the default Markdown theme. No <meta> or conditional
# comments, so the result passes the validator without trusted_template.
_DEFAULT_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<title>{title}</title>
<style>
body {{ margin: 0; padding: 24px 12px; background: #f6f6f4; color: #1a1a1a;
  font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; font-size: 16px; line-height: 1.6; }}
.wrap {{ max-width: 640px; margin: 0 auto; background: #ffffff; padding: 24px 28px; border-radius: 6px; }}
h1, h2, h3 {{ line-height: 1.3; margin: 1.4em 0 0.5em; }}
h1 {{ font-size: 24px; }} h2 {{ font-size: 20px; }} h3 {{ font-size: 17px; }}
p, ul, ol, blockquote, pre {{ margin: 0 0 1em; }}
a {{ color: #0b5cad; }}
code {{ font-family: Menlo, Consolas, monospace; font-size: 14px; background: #f0f0ee; padding: 1px 4px; border-radius: 3px; }}
pre {{ background: #f0f0ee; padding: 12px; border-radius: 4px; overflow-x: auto; }}
pre code {{ padding: 0; background: transparent; }}
blockquote {{ border-left: 3px solid #d0d0cc; padding-left: 12px; color: #555555; }}
hr {{ border: 0; border-top: 1px solid #e0e0dc; }}
img {{ max-width: 100%; }}
</style>
</head>
<body><div class="wrap">
{content}
</div></body>
</html>
"""


@dataclass(frozen=True)
class Rendered:
    text: str
    html: str | None
    items: dict[str, int] | None = None


def _require_clean(html: str, trusted_template: bool = False) -> None:
    violations = find_violations(html, trusted_template=trusted_template)
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


@lru_cache(maxsize=None)
def shimbun_sections() -> tuple[dict, ...]:
    with open(THEMES_DIR / "shimbun" / "theme.yaml", encoding="utf-8") as f:
        return tuple(yaml.safe_load(f)["sections"])


def render_markdown_default(source: str, title: str) -> str:
    fragment = nh3.clean(_md.render(source))
    page = _DEFAULT_TEMPLATE.format(title=htmllib.escape(title), content=fragment)
    out = _inliner.inline(page)
    _require_clean(out)
    return out


def render_shimbun(source: str) -> tuple[str, dict[str, int]]:
    sections = list(shimbun_sections())
    try:
        digest = parse_dialect(source, [s["label"] for s in sections])
    except DialectError as e:
        raise TegamiError("E_THEME_PARSE", str(e))
    except yaml.YAMLError as e:
        # The vendored parser lets front-matter YAML errors through unwrapped.
        mark = getattr(e, "problem_mark", None)
        line = mark.line + 2 if mark is not None else 2  # +1 for 1-based, +1 for the opening '---'
        raise TegamiError("E_THEME_PARSE", f"line {line}: invalid front matter YAML")
    out = render_html(digest, sections)
    _require_clean(out, trusted_template=True)
    return out, {label: len(items) for label, items in digest.sections}


def render(source: str, content_type: str, theme: str | None, title: str) -> Rendered:
    if content_type == "plain":
        return Rendered(text=source, html=None)
    if content_type == "html":
        _require_clean(source)
        return Rendered(text=html_to_text(source), html=source)
    if theme == "shimbun":
        out, items = render_shimbun(source)
        return Rendered(text=source, html=out, items=items)
    return Rendered(text=source, html=render_markdown_default(source, title))
