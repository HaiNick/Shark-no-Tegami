import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tegami.config import Config, parse_ref_allowlist  # noqa: E402
from tegami.pipeline import Relay  # noqa: E402
from tegami.render import render  # noqa: E402
from tegami.themes import ThemeStore  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
THEMES = Path(__file__).resolve().parents[1] / "themes"
NO_OVERRIDE = Path("/nonexistent-tegami-overrides")


def render_theme(source, content_type, theme, subject, store=None):
    """render() against the repo's built-in themes, resolving the theme by name."""
    store = store or ThemeStore(NO_OVERRIDE, THEMES)
    return render(source, content_type, store.resolve(theme), subject, store)
SHA = "0123456789abcdef0123456789abcdef01234567"
REF = f"HaiNick/shimbun-archive@{SHA}:digests/2026-W44.md"


@pytest.fixture
def cfg():
    return Config(
        allowed_recipients=frozenset({"01nicklas07@gmail.com", "nicklas@snowy-burbot.com"}),
        ref_allowlist=parse_ref_allowlist("HaiNick/shimbun-archive:digests/"),
        github_token="test-token",
        max_body_bytes=4096,
        ref_timeout_s=1.0,
        themes_dir=NO_OVERRIDE,
        themes_default_dir=THEMES,
    )


@pytest.fixture
def relay():
    return Relay(mail_from="tegami@snowy-burbot.com", host="sharky", port=25)


@pytest.fixture
def digest_md():
    return (FIXTURES / "digest.md").read_text(encoding="utf-8")


class SpySender:
    def __init__(self, exc: Exception | None = None):
        self.calls = []
        self.exc = exc

    async def __call__(self, msg, **kwargs):
        self.calls.append((msg, kwargs))
        if self.exc:
            raise self.exc


@pytest.fixture
def sender():
    return SpySender()
