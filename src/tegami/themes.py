"""Themes: Jinja templates and theme.yaml, resolved per file from the host
override directory first, then the image defaults.

Python here only parses input, builds the template context, and maps errors.
Every piece of HTML lives in a theme's templates.
"""

import html as htmllib
import json
import re
import traceback
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace as NS

import css_inline
import nh3
import yaml
from jinja2 import ChoiceLoader, FileSystemLoader, StrictUndefined, TemplateSyntaxError
from jinja2.sandbox import ImmutableSandboxedEnvironment
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from markdown_it import MarkdownIt
from markupsafe import Markup

from .errors import TegamiError
from .validate import find_violations
from .vendor.shimbun_lib import ALLOWED_FLAGS, DialectError, parse_dialect

NAME_RE = re.compile(r"^[a-z0-9-]{1,32}$")
# theme input -> the content_type it accepts
INPUTS = {"shimbun": "markdown", "markdown": "markdown", "json": "json"}
THEME_FILE = "theme.yaml"
HTML_TEMPLATE = "email.html.j2"
TEXT_TEMPLATE = "email.txt.j2"
SCHEMA_FILE = "schema.json"
MAX_DETAILS = 20

_md = MarkdownIt("commonmark", {"html": False})
_inliner = css_inline.CSSInliner(load_remote_stylesheets=False)


def _template_error(message: str) -> TegamiError:
    return TegamiError("E_THEME_TEMPLATE", message)


@dataclass(frozen=True)
class Theme:
    name: str
    input: str
    config: dict
    allowed_recipients: frozenset[str] | None
    inline_css: bool


@dataclass(frozen=True)
class ThemeOutput:
    html: str
    text: str | None  # None: the caller derives the text part from the HTML
    items: dict[str, int] | None = None


def _autoescape(name: str | None) -> bool:
    # Plain-text templates are not HTML; everything else is escaped.
    return not (name or "").endswith(".txt.j2")


class ThemeStore:
    def __init__(self, override_dir: Path, default_dir: Path):
        self.dirs = (Path(override_dir), Path(default_dir))
        # cache_size=0: every render resolves and compiles from disk, so an
        # edited, added, or deleted override applies on the next send.
        self.env = ImmutableSandboxedEnvironment(
            loader=ChoiceLoader([FileSystemLoader(str(d)) for d in self.dirs]),
            autoescape=_autoescape,
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
            auto_reload=True,
            cache_size=0,
        )

    # -- lookup ------------------------------------------------------------

    def find(self, theme: str, filename: str) -> Path | None:
        for d in self.dirs:
            path = d / theme / filename
            if path.is_file():
                return path
        return None

    def label(self, path: str | Path) -> str:
        path = Path(path)
        for d, kind in zip(self.dirs, ("override", "default")):
            try:
                return f"{path.relative_to(d)} ({kind})"
            except ValueError:
                continue
        return path.name

    def available(self) -> list[str]:
        names = set()
        for d in self.dirs:
            if d.is_dir():
                names.update(p.name for p in d.iterdir() if p.is_dir() and NAME_RE.match(p.name))
        return sorted(n for n in names if self.find(n, THEME_FILE) and self.find(n, HTML_TEMPLATE))

    def resolve(self, name: str) -> Theme:
        if not NAME_RE.match(name or ""):
            raise TegamiError("E_ARGS", "theme must match ^[a-z0-9-]{1,32}$")
        if not (self.find(name, THEME_FILE) and self.find(name, HTML_TEMPLATE)):
            raise TegamiError("E_ARGS", f"unknown theme {name!r}; available: {', '.join(self.available()) or 'none'}")
        return self._load_theme(name)

    def _load_theme(self, name: str) -> Theme:
        path = self.find(name, THEME_FILE)
        where = self.label(path)
        try:
            config = yaml.safe_load(path.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            raise _template_error(f"{where}: not valid UTF-8")
        except yaml.YAMLError as e:
            mark = getattr(e, "problem_mark", None)
            line = f" line {mark.line + 1}" if mark is not None else ""
            raise _template_error(f"{where}{line}: invalid YAML")
        if not isinstance(config, dict):
            raise _template_error(f"{where}: must be a mapping")
        kind = config.get("input")
        if kind not in INPUTS:
            raise _template_error(f"{where}: 'input' must be one of {', '.join(INPUTS)}")
        inline_css = config.get("inline_css", False)
        if not isinstance(inline_css, bool):
            raise _template_error(f"{where}: 'inline_css' must be true or false")

        allowed = config.get("allowed_recipients")
        if allowed is not None:
            if not isinstance(allowed, list) or not all(isinstance(a, str) for a in allowed):
                raise _template_error(f"{where}: 'allowed_recipients' must be a list of addresses")
            allowed = frozenset(a.strip().lower() for a in allowed if a.strip())

        if kind == "shimbun":
            self._check_shimbun_config(config, where)
        return Theme(name, kind, config, allowed, inline_css)

    @staticmethod
    def _check_shimbun_config(config: dict, where: str) -> None:
        sections = config.get("sections")
        if not isinstance(sections, list) or not sections:
            raise _template_error(f"{where}: 'sections' must be a non-empty list")
        for i, s in enumerate(sections):
            missing = [k for k in ("key", "label", "stat", "chip", "color") if not isinstance(s, dict) or not isinstance(s.get(k), str)]
            if missing:
                raise _template_error(f"{where}: sections[{i}] needs string {', '.join(missing)}")
        for key in ("badge_styles",):
            if not isinstance(config.get(key, {}), dict):
                raise _template_error(f"{where}: '{key}' must be a mapping")
        for key in ("footer", "chip_style_default", "badge_style_default"):
            if not isinstance(config.get(key, ""), str):
                raise _template_error(f"{where}: '{key}' must be a string")

    # -- rendering ---------------------------------------------------------

    def _location(self, exc: BaseException, fallback: str) -> str:
        # Jinja rewrites tracebacks so template frames carry the template file and line.
        frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename.endswith(".j2")]
        if frames:
            return f"{self.label(frames[-1].filename)} line {frames[-1].lineno}"
        return fallback

    def _run(self, theme: str, filename: str, context: dict | None) -> str:
        """Compile (context None) or render one template, mapping every failure to E_THEME_TEMPLATE."""
        name = f"{theme}/{filename}"
        try:
            template = self.env.get_template(name)
            return "" if context is None else template.render(context)
        except TemplateSyntaxError as e:
            where = self.label(e.filename) if e.filename else name
            raise _template_error(f"{where} line {e.lineno}: {e.message}")
        except TegamiError:
            raise
        except Exception as e:
            raise _template_error(f"{self._location(e, name)}: {type(e).__name__}: {e}")

    def render(self, theme: Theme, source: str, subject: str) -> ThemeOutput:
        items = None
        default_text: str | None = source
        if theme.input == "shimbun":
            context, items = self._shimbun_context(theme, source)
        elif theme.input == "markdown":
            context = _markdown_context(source)
        else:
            context = {"data": self._validated_json(theme, source)}
            default_text = None
        context.update(subject=subject, theme=theme.config)

        html = self._run(theme.name, HTML_TEMPLATE, context)
        if theme.inline_css:
            try:
                html = _inliner.inline(html)
            except Exception as e:
                raise _template_error(f"{theme.name}/{HTML_TEMPLATE}: CSS inlining failed: {e}")
        # Templates are trusted only for conditional comments, <meta> without
        # http-equiv, and <style>. Everything else is still rejected.
        violations = find_violations(html, trusted_template=True)
        if violations:
            noun = "violation" if len(violations) == 1 else "violations"
            raise TegamiError("E_UNSAFE_CONTENT", f"{len(violations)} {noun}", violations)

        text = self._run(theme.name, TEXT_TEMPLATE, context) if self.find(theme.name, TEXT_TEMPLATE) else default_text
        return ThemeOutput(html=html, text=text, items=items)

    # -- inputs ------------------------------------------------------------

    @staticmethod
    def _shimbun_context(theme: Theme, source: str) -> tuple[dict, dict[str, int]]:
        cfg = theme.config
        sections_cfg = cfg["sections"]
        try:
            d = parse_dialect(source, [s["label"] for s in sections_cfg])
        except DialectError as e:
            raise TegamiError("E_THEME_PARSE", str(e))

        by_label = {s["label"]: s for s in sections_cfg}
        counts = {s["key"]: 0 for s in sections_cfg}
        badge_styles = cfg.get("badge_styles", {})
        badge_default = cfg.get("badge_style_default", "background:#eee;color:#333")
        chip_default = cfg.get("chip_style_default", "background:#eee;color:#333")

        sections = []
        for label, items in d.sections:
            s = by_label[label]
            counts[s["key"]] = len(items)
            sections.append(NS(
                label=label,
                key=s["key"],
                color=s["color"],
                items=[
                    NS(
                        title=it.title, url=it.url, tldr=it.tldr, source=it.source, date=it.date,
                        badges=[NS(name=b, style=badge_styles.get(b, badge_default), warn=b in ALLOWED_FLAGS) for b in it.badges],
                    )
                    for it in items
                ],
            ))
        stats = [
            NS(
                key=s["key"], label=s["label"], stat=s["stat"], chip=s["chip"], color=s["color"],
                chip_style=s.get("chip_style", chip_default), legend=s.get("legend", s["label"]),
                count=counts[s["key"]],
            )
            for s in sections_cfg
        ]
        cutoff_display = d.cutoff.replace("T", " ")[:16]
        # The footer is trusted HTML from theme.yaml. Markup.replace escapes the
        # inserted values; str.format is avoided because it allows attribute access.
        footer = (
            Markup(cfg.get("footer", ""))
            .replace("{title}", d.title)
            .replace("{issue}", d.issue)
            .replace("{cutoff}", cutoff_display)
        )
        context = {
            "digest": NS(title=d.title, issue=d.issue, range=d.range, cutoff=d.cutoff, tldr=d.tldr, errors=list(d.errors)),
            "sections": sections,
            "stats": stats,
            "cutoff_display": cutoff_display,
            "footer": footer,
        }
        return context, {label: len(items) for label, items in d.sections}

    def _load_schema(self, theme: Theme) -> tuple[dict, Path]:
        path = self.find(theme.name, SCHEMA_FILE)
        if path is None:
            raise _template_error(f"{theme.name}/{SCHEMA_FILE} is missing (required for input: json)")
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
        except (ValueError, SchemaError) as e:
            raise _template_error(f"{self.label(path)}: invalid schema: {str(e).splitlines()[0][:200]}")
        return schema, path

    def _validated_json(self, theme: Theme, source: str) -> dict:
        schema, path = self._load_schema(theme)
        data = parse_json_object(source)
        try:
            errors = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: list(map(str, e.absolute_path)))
        except Exception as e:  # e.g. an unresolvable $ref; remote refs are never fetched
            raise _template_error(f"{self.label(path)}: {type(e).__name__}: {e}")
        if errors:
            details = [f"{e.json_path}: {e.message[:200]}" for e in errors[:MAX_DETAILS]]
            raise TegamiError("E_DATA_INVALID", details[0], details)
        return data

    # -- startup -----------------------------------------------------------

    def self_check(self, shimbun_sample: str) -> list[str]:
        """Load every theme; render input: shimbun themes with the sample digest,
        compile the others. Returns one line per broken theme."""
        problems = []
        for name in self.available():
            try:
                theme = self._load_theme(name)
                if theme.input == "shimbun":
                    self.render(theme, shimbun_sample, "Tegami startup check")
                    continue
                self._run(name, HTML_TEMPLATE, None)
                if self.find(name, TEXT_TEMPLATE):
                    self._run(name, TEXT_TEMPLATE, None)
                if theme.input == "json":
                    self._load_schema(theme)
            except TegamiError as e:
                problems.append(f"theme {name!r}: {e.code}: {e.message}")
        return problems


# -- input parsers (module level: no theme state) ---------------------------


def parse_json_object(source: str) -> dict:
    def reject(constant: str):
        raise ValueError(f"{constant} is not allowed")

    try:
        data = json.loads(source, parse_constant=reject)
    except json.JSONDecodeError as e:
        raise TegamiError("E_DATA_INVALID", f"$: invalid JSON at line {e.lineno} column {e.colno}: {e.msg}")
    except (ValueError, RecursionError) as e:
        raise TegamiError("E_DATA_INVALID", f"$: {e}" if isinstance(e, ValueError) else "$: nested too deeply")
    if not isinstance(data, dict):
        raise TegamiError("E_DATA_INVALID", "$: body must be a JSON object")
    return data


def split_front_matter(source: str) -> tuple[dict, str]:
    """Front matter exists only if line 1 is exactly '---' and a closing '---' line follows."""
    lines = source.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return {}, source
    for end in range(1, len(lines)):
        if lines[end].rstrip("\r\n") == "---":
            break
    else:
        return {}, source
    try:
        meta = yaml.safe_load("".join(lines[1:end]))
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        line = mark.line + 2 if mark is not None else 2  # +1 for 1-based, +1 for the opening '---'
        raise TegamiError("E_THEME_PARSE", f"line {line}: front matter is not valid YAML")
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        raise TegamiError("E_THEME_PARSE", "line 2: front matter must be a mapping")
    return meta, "".join(lines[end + 1:])


def _clean(markdown: str) -> Markup:
    # nh3 has already sanitized it, so the template may print it unescaped.
    return Markup(nh3.clean(_md.render(markdown)))


def _markdown_context(source: str) -> dict:
    meta, body = split_front_matter(source)
    lines = body.splitlines(keepends=True)
    tokens = _md.parse(body)
    heads = []  # (first line, line after the heading, plain-text title)
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open" and tok.tag == "h2" and tok.level == 0 and tok.map:
            inline = tokens[i + 1].content if i + 1 < len(tokens) else ""
            title = htmllib.unescape(nh3.clean(_md.renderInline(inline), tags=set()))
            heads.append((tok.map[0], tok.map[1], title))

    first = heads[0][0] if heads else len(lines)
    sections = []
    for n, (_, body_start, title) in enumerate(heads):
        stop = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
        sections.append(NS(title=title, html=_clean("".join(lines[body_start:stop]))))
    return {
        "meta": meta,
        "content": _clean(body),
        "intro": _clean("".join(lines[:first])),
        "sections": sections,
    }
