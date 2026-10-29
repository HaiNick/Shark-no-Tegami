# Vendored from the shimbun-digest skill (scripts/shimbun_lib.py), unchanged below this header.
# Tegami uses only parse_dialect(), render_html(), DialectError, Digest, and DItem.
# Do not edit this copy: make changes upstream in the skill first, then re-vendor.
"""Shared code for the shimbun-digest scripts.

Contents:
- config and run-directory helpers
- raw tool-output parsing and normalization (Kari fetch_feed, Kari fetch_page
  for HN Algolia, Yami kev_recent text)
- Markdown escaping and the Shimbun Markdown dialect (write and strict parse)
- the email HTML renderer (port of the v1 template)

The dialect parser and HTML renderer are deliberately self-contained so
Shark-no-Tegami can vendor this file for its `theme: shimbun` renderer.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from zoneinfo import ZoneInfo

SKILL_DIR = Path(__file__).resolve().parent.parent
RUN_DIR = Path(os.environ.get("SHIMBUN_RUN_DIR", "shimbun-run")).resolve()

ALLOWED_FLAGS = ("patch now", "actively exploited", "security release")
RELEASE_TYPES = ("stable", "pre-release")
BADGE_ORDER = ALLOWED_FLAGS + RELEASE_TYPES
PRERELEASE_RE = re.compile(r"(?<![a-z])(alpha|beta|rc|dev|preview|nightly|canary|pre)(?![a-z])", re.I)


# --------------------------------------------------------------------------
# Config and paths
# --------------------------------------------------------------------------

def load_config() -> dict:
    import yaml  # PyYAML, preinstalled in the sandbox

    path = Path(os.environ.get("SHIMBUN_SOURCES", SKILL_DIR / "sources.yaml"))
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    ids = [s["id"] for s in cfg["sources"]]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        die(f"sources.yaml: duplicate source ids {sorted(dupes)}")
    keys = {s["key"] for s in cfg["sections"]}
    for s in cfg["sources"]:
        if s["section"] not in keys:
            die(f"sources.yaml: source {s['id']} has unknown section {s['section']}")
    return cfg


def die(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def read_json(path: Path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def load_run() -> dict:
    p = RUN_DIR / "run.json"
    if not p.exists():
        die(f"{p} not found. Run `collect.py init` first.")
    return read_json(p)


# --------------------------------------------------------------------------
# Time helpers
# --------------------------------------------------------------------------

def parse_dt(value: str) -> datetime | None:
    """Parse ISO 8601 or RFC 822 dates. Returns an aware datetime or None."""
    if not value:
        return None
    v = value.strip()
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(v)
        except (TypeError, ValueError, IndexError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def fmt_short(dt: datetime, tz: ZoneInfo) -> str:
    d = dt.astimezone(tz)
    return f"{d.strftime('%b')} {d.day}"


def fmt_range(start: datetime, end: datetime) -> str:
    return f"{start.strftime('%b')} {start.day} - {end.strftime('%b')} {end.day}, {end.year}"


# --------------------------------------------------------------------------
# URL handling
# --------------------------------------------------------------------------

_TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|mc_cid$|mc_eid$|ref$|ref_src$)", re.I)


def clean_url(url: str) -> str | None:
    """Return an https URL safe for a Markdown link destination, or None."""
    if not url:
        return None
    url = html.unescape(url.strip())
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _TRACKING.match(k)])
    out = urlunsplit(("https", parts.netloc, parts.path, query, ""))
    # characters that would end or break a Markdown link destination
    for ch, rep in ((" ", "%20"), ("(", "%28"), (")", "%29"), ("<", "%3C"), (">", "%3E"), ('"', "%22")):
        out = out.replace(ch, rep)
    return out


def dedupe_key(url: str) -> str:
    p = urlsplit(url)
    return f"{p.netloc.lower().removeprefix('www.')}{p.path.rstrip('/')}?{p.query}"


def item_id(url: str) -> str:
    return hashlib.sha1(dedupe_key(url).encode()).hexdigest()[:10]


# --------------------------------------------------------------------------
# Raw tool output parsing
# --------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: str, limit: int = 500) -> str:
    t = html.unescape(_TAG_RE.sub(" ", text or ""))
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= limit else t[: limit - 3].rstrip() + "..."


def unwrap(payload):
    """Peel the MCP wrappers: {"result": "<json or text>"} and HTML bodies."""
    for _ in range(4):
        if isinstance(payload, dict) and set(payload) == {"result"}:
            payload = payload["result"]
            continue
        if isinstance(payload, str):
            s = payload.strip()
            m = re.fullmatch(r"(?s)<html><body>(.*)</body></html>", s)
            if m:
                s = m.group(1).strip()
            if s[:1] in "{[":
                try:
                    payload = json.loads(s)
                    continue
                except json.JSONDecodeError:
                    pass
            return s
        return payload
    return payload


def load_raw(path: Path):
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = text  # kev_recent text saved as is
    return unwrap(data)


def feed_items(payload) -> list[dict]:
    """Kari fetch_feed output, or a bare list the model saved."""
    if isinstance(payload, dict):
        payload = payload.get("items", [])
    if not isinstance(payload, list):
        raise ValueError("expected fetch_feed JSON with an items list")
    return payload


KEV_LINE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})\s*\|\s*(CVE-\d{4}-\d{4,})\s*\|\s*(.+?)\s*$")


def kev_items(payload) -> list[dict]:
    """Parse Yami kev_recent text: 'YYYY-MM-DD | CVE-... | Vendor Product -- Name'."""
    if isinstance(payload, (dict, list)):
        payload = json.dumps(payload)
    out = []
    for line in str(payload).splitlines():
        m = KEV_LINE.match(line)
        if not m:
            continue
        date, cve, rest = m.groups()
        product, _, name = rest.partition(" -- ")
        out.append({"date": date, "cve": cve, "product": product.strip(), "name": (name or product).strip()})
    return out


def hn_hits(payload) -> list[dict]:
    if isinstance(payload, dict) and "hits" in payload:
        return payload["hits"]
    raise ValueError("expected HN Algolia JSON with a hits list")


# --------------------------------------------------------------------------
# Item model
# --------------------------------------------------------------------------

@dataclass
class Item:
    id: str
    section: str
    source_id: str
    source_abbrev: str
    title: str
    url: str
    published: str
    summary: str = ""
    release_type: str | None = None
    kev_cves: list[str] = field(default_factory=list)
    hn_object_id: str | None = None
    points: int | None = None
    shown_summary: bool = False  # True when the model has not seen the summary yet

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# Markdown escaping
# --------------------------------------------------------------------------

_INLINE_ESC = re.compile(r"([\\`*_\[\]])")
_UNESC = re.compile(r"\\([\\`*_\[\]#>+\-.!|~])")


def md_inline(text: str) -> str:
    """Escape text for use inside a heading link label or a paragraph."""
    t = re.sub(r"\s+", " ", text).strip()
    t = _INLINE_ESC.sub(r"\\\1", t)
    # a paragraph must not turn into another block type
    t = re.sub(r"^([#>+\-])", r"\\\1", t)
    t = re.sub(r"^(\d+)([.)])", r"\1\\\2", t)
    return t


def md_unescape(text: str) -> str:
    return _UNESC.sub(r"\1", text)


# --------------------------------------------------------------------------
# Shimbun Markdown dialect
# --------------------------------------------------------------------------

FRONT_KEYS = ("title", "issue", "range", "cutoff")


@dataclass
class DItem:
    title: str
    url: str
    badges: list[str]
    tldr: str
    source: str
    date: str


@dataclass
class Digest:
    title: str
    issue: str
    range: str
    cutoff: str
    tldr: str
    sections: list[tuple[str, list[DItem]]]  # (label, items) in document order
    errors: list[str]


def write_dialect(d: Digest) -> str:
    def q(v: str) -> str:
        return json.dumps(v, ensure_ascii=False)  # JSON strings are valid YAML

    out = ["---"]
    for k in FRONT_KEYS:
        out.append(f"{k}: {q(getattr(d, k))}")
    out += ["---", "", f"> {md_inline(d.tldr)}", ""]
    for label, items in d.sections:
        if not items:
            continue
        out += [f"## {label}", ""]
        for it in items:
            out.append(f"### [{md_inline(it.title)}]({it.url})")
            if it.badges:
                out.append(" ".join(f"`{b}`" for b in it.badges))
            out.append(md_inline(it.tldr))
            out.append(f"*{md_inline(it.source)} · {it.date}*")
            out.append("")
    if d.errors:
        out += ["## Errors", ""]
        out += [f"- {md_inline(e)}" for e in d.errors]
        out.append("")
    return "\n".join(out).rstrip() + "\n"


class DialectError(ValueError):
    def __init__(self, line: int, msg: str):
        super().__init__(f"line {line}: {msg}")
        self.line = line


_H3 = re.compile(r"^### \[((?:\\.|[^\\\]])+)\]\((https://[^\s()<>]+)\)$")
_BADGES = re.compile(r"^`[^`]+`(?: `[^`]+`)*$")
_META = re.compile(r"^\*((?:\\.|[^*\\])+) · ([A-Z][a-z]{2} \d{1,2})\*$")


def parse_dialect(text: str, section_labels: list[str]) -> Digest:
    """Strict parser. Raises DialectError with a 1-based line number."""
    import yaml

    lines = text.splitlines()
    n = len(lines)
    i = 0

    def peek(k: int = 0) -> str | None:
        return lines[i + k] if i + k < n else None

    if peek() != "---":
        raise DialectError(1, "expected front matter '---'")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise DialectError(1, "front matter not closed")
    fm = yaml.safe_load("\n".join(lines[1:end])) or {}
    if not isinstance(fm, dict) or set(fm) != set(FRONT_KEYS):
        raise DialectError(2, f"front matter keys must be exactly {list(FRONT_KEYS)}")
    fm = {k: str(v) for k, v in fm.items()}
    i = end + 1

    while peek() == "":
        i += 1
    if not (peek() or "").startswith("> "):
        raise DialectError(i + 1, "expected TL;DR blockquote")
    tldr_parts = []
    while (peek() or "").startswith("> "):
        tldr_parts.append(peek()[2:])
        i += 1
    tldr = md_unescape(" ".join(tldr_parts))

    allowed = list(section_labels) + ["Errors"]
    sections: list[tuple[str, list[DItem]]] = []
    errors: list[str] = []
    last_pos = -1
    while i < n:
        line = peek()
        if line == "":
            i += 1
            continue
        if not line.startswith("## "):
            raise DialectError(i + 1, f"expected '## SECTION', got {line[:40]!r}")
        label = line[3:]
        if label not in allowed:
            raise DialectError(i + 1, f"unknown section {label!r}")
        pos = allowed.index(label)
        if pos <= last_pos:
            raise DialectError(i + 1, f"section {label!r} out of order or repeated")
        last_pos = pos
        i += 1
        if label == "Errors":
            while i < n and peek() == "":
                i += 1
            while i < n and (peek() or "").startswith("- "):
                errors.append(md_unescape(peek()[2:]))
                i += 1
            continue
        items: list[DItem] = []
        while True:
            while i < n and peek() == "":
                i += 1
            if i >= n or (peek() or "").startswith("## "):
                break
            m = _H3.match(peek())
            if not m:
                raise DialectError(i + 1, "expected '### [HEADLINE](https://URL)'")
            title, url = md_unescape(m.group(1)), m.group(2)
            i += 1
            badges: list[str] = []
            if _BADGES.match(peek() or ""):
                badges = re.findall(r"`([^`]+)`", peek())
                bad = [b for b in badges if b not in BADGE_ORDER]
                if bad:
                    raise DialectError(i + 1, f"unknown badge(s) {bad}")
                if "stable" in badges and "pre-release" in badges:
                    raise DialectError(i + 1, "both stable and pre-release")
                i += 1
            body = peek()
            if not body or body.startswith(("#", "*", "`")):
                raise DialectError(i + 1, "expected item TL;DR paragraph")
            i += 1
            mm = _META.match(peek() or "")
            if not mm:
                raise DialectError(i + 1, "expected meta line '*SOURCE · Mon D*'")
            i += 1
            items.append(DItem(title, url, badges, md_unescape(body), md_unescape(mm.group(1)), mm.group(2)))
        if not items:
            raise DialectError(i, f"section {label!r} has no items")
        sections.append((label, items))
    return Digest(fm["title"], fm["issue"], fm["range"], fm["cutoff"], tldr, sections, errors)


# --------------------------------------------------------------------------
# Email HTML (v1 template port)
# --------------------------------------------------------------------------

def _e(s: str) -> str:
    return html.escape(s, quote=True)


def render_html(d: Digest, sections_cfg: list[dict]) -> str:
    """Render a parsed Digest into the v1 email layout. All text is escaped."""
    by_label = {s["label"]: s for s in sections_cfg}
    counts = {s["key"]: 0 for s in sections_cfg}
    for label, items in d.sections:
        counts[by_label[label]["key"]] = len(items)

    stats = "".join(
        f'<td align="right" style="padding-left:16px">'
        f'<div style="font-size:22px;font-weight:bold;color:#1a1a1a;line-height:1">{counts[s["key"]]}</div>'
        f'<div style="font-size:10px;color:#888;text-transform:uppercase;letter-spacing:.06em">{_e(s["stat"])}</div></td>'
        for s in sections_cfg
    )
    legend = "".join(
        f'<td style="padding-right:16px"><table cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td width="3" style="width:3px;background:{s["color"]};font-size:1px;line-height:1px">&nbsp;</td>'
        f'<td style="padding-left:6px;font-size:11px;color:#666;white-space:nowrap">{_e(s["label"].replace("New KEV entries", "KEV").replace("Releases", "Release").replace("Homelab & open source", "Homelab"))}</td>'
        f"</tr></table></td>"
        for s in sections_cfg
    )
    chip_style = {
        "security": "background:#fcebeb;color:#791f1f",
        "kev": "background:#faece7;color:#712b13",
        "releases": "background:#eaf3de;color:#27500a",
        "homelab": "background:#eeedfe;color:#26215c",
        "tech": "background:#e0f4f6;color:#0a4a53",
        "ai": "background:#f0eafe;color:#4a1fa8",
    }
    chips = "".join(
        f'<span style="font-size:11px;padding:2px 8px;{chip_style.get(s["key"], "background:#eee;color:#333")};margin-right:6px">'
        f'{counts[s["key"]]} {_e(s["chip"])}</span>'
        for s in sections_cfg
    )

    badge_style = {
        "patch now": "background:#faeeda;color:#633806",
        "actively exploited": "background:#faeeda;color:#633806",
        "security release": "background:#faeeda;color:#633806",
        "stable": "background:#eaf3de;color:#27500a",
        "pre-release": "background:#eeedfe;color:#3c3489",
    }

    rows = []
    for label, items in d.sections:
        color = by_label[label]["color"]
        rows.append(
            '<tr><td style="padding:6px 20px;background:#f9f9f8;border-bottom:1px solid #eee;border-top:1px solid #eee">'
            f'<div style="font-size:10px;font-weight:bold;text-transform:uppercase;letter-spacing:.08em;color:#888">{_e(label)}</div></td></tr>'
        )
        for it in items:
            badges = ""
            if it.badges:
                spans = "".join(
                    f'<span style="font-size:10px;font-weight:bold;padding:2px 6px;{badge_style[b]};white-space:nowrap;margin-right:4px">'
                    f'{"&#9888; " if b in ALLOWED_FLAGS else ""}{_e(b)}</span>'
                    for b in it.badges
                )
                badges = f'<div style="margin-bottom:4px">{spans}</div>'
            rows.append(
                '<tr><td style="padding:10px 20px;border-bottom:1px solid #eee">'
                '<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
                '<td width="3" style="vertical-align:top;padding-right:12px">'
                f'<!--[if mso]><table cellpadding="0" cellspacing="0" border="0"><tr><td width="3" bgcolor="{color}" style="width:3px;font-size:1px;line-height:1px">&nbsp;</td></tr></table><![endif]-->'
                f'<!--[if !mso]><!--><div style="width:3px;min-height:50px;background:{color}">&nbsp;</div><!--<![endif]-->'
                "</td>"
                '<td style="vertical-align:top">'
                f'<div style="font-size:13px;font-weight:500;color:#1a1a1a;margin-bottom:4px"><a href="{_e(it.url)}" style="color:#1a1a1a;text-decoration:none">{_e(it.title)}</a></div>'
                f"{badges}"
                f'<div style="font-size:11px;color:#666;line-height:1.5;margin-bottom:3px">{_e(it.tldr)}</div>'
                f'<div style="font-size:11px;color:#aaa">{_e(it.source)} &middot; {_e(it.date)}</div>'
                "</td></tr></table></td></tr>"
            )

    errors = ""
    if d.errors:
        lines = "".join(f'<div style="font-size:12px;color:#854f0b;line-height:1.6">{_e(e)}</div>' for e in d.errors)
        errors = (
            '<tr><td style="padding:16px 20px"><table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
            '<td style="background:#faeeda;border:1px solid #ef9f27;padding:12px 14px">'
            '<div style="font-size:10px;font-weight:bold;text-transform:uppercase;letter-spacing:.08em;color:#633806;margin-bottom:6px">Errors this run</div>'
            f"{lines}</td></tr></table></td></tr>"
        )

    cutoff = d.cutoff.replace("T", " ")[:16]
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_e(d.title)} {_e(d.issue)}</title>
</head>
<body style="margin:0;padding:0;background:#f4f4f2;font-family:Arial,Helvetica,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f4f4f2;padding:24px 0">
<tr><td align="center">
<table width="640" cellpadding="0" cellspacing="0" border="0" style="background:#ffffff;border:1px solid #e8e8e6">
<tr><td style="padding:20px;border-bottom:1px solid #eee"><table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
<td><div style="font-size:14px;font-weight:bold;color:#1a1a1a">{_e(d.title)}</div><div style="font-size:12px;color:#888;margin-top:2px">{_e(d.range)}</div></td>
<td align="right"><table cellpadding="0" cellspacing="0" border="0"><tr>{stats}</tr></table></td>
</tr></table></td></tr>
<tr><td style="padding:8px 20px;background:#f9f9f8;border-bottom:1px solid #eee"><table cellpadding="0" cellspacing="0" border="0"><tr>{legend}</tr></table></td></tr>
<tr><td style="padding:16px 20px;border-bottom:1px solid #eee"><table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
<td style="background:#f4f4f2;padding:14px 16px">
<div style="font-size:10px;font-weight:bold;text-transform:uppercase;letter-spacing:.08em;color:#888;margin-bottom:6px">TL;DR: this week</div>
<div style="font-size:13px;color:#1a1a1a;line-height:1.6">{_e(d.tldr)}</div>
<div style="margin-top:10px">{chips}</div>
</td></tr></table></td></tr>
{"".join(rows)}
{errors}
<tr><td style="padding:8px 20px;background:#f9f9f8;border-top:1px solid #eee">
<div style="font-size:11px;color:#aaa">Generated by Claude Code Routine &middot; {_e(d.title)} &middot; {_e(d.issue)} &middot; Cutoff: {_e(cutoff)} Europe/Berlin</div>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""
