"""TEGAMI_* settings, read once at startup."""

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger("shark-no-tegami")


@dataclass(frozen=True)
class Config:
    # None means no allowlist: any recipient is accepted.
    allowed_recipients: frozenset[str] | None = None
    # (owner/repo lowercased, path prefix) pairs. Empty means body_ref is disabled.
    ref_allowlist: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    github_token: str = ""
    max_body_bytes: int = 262144
    ref_timeout_s: float = 10.0


def _split(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def parse_ref_allowlist(value: str) -> tuple[tuple[str, str], ...]:
    pairs = []
    for entry in _split(value):
        repo, sep, prefix = entry.partition(":")
        if not sep or repo.count("/") != 1 or not prefix or prefix.startswith("/") or ".." in prefix.split("/"):
            raise RuntimeError(f"TEGAMI_REF_ALLOWLIST entry {entry!r} is not OWNER/REPO:PATH_PREFIX")
        # Match whole path segments: "digests" must not admit "digests-old/x.md".
        if not prefix.endswith("/"):
            prefix += "/"
        pairs.append((repo.lower(), prefix))
    return tuple(pairs)


def load_config(env=os.environ) -> Config:
    raw_recipients = env.get("TEGAMI_ALLOWED_RECIPIENTS", "")
    if raw_recipients.strip():
        allowed = frozenset(addr.lower() for addr in _split(raw_recipients))
    else:
        allowed = None
        logger.warning("TEGAMI_ALLOWED_RECIPIENTS is unset: send_email accepts any recipient")

    ref_allowlist = parse_ref_allowlist(env.get("TEGAMI_REF_ALLOWLIST", ""))
    if not ref_allowlist:
        logger.info("TEGAMI_REF_ALLOWLIST is unset: body_ref is disabled")

    token = env.get("TEGAMI_GITHUB_TOKEN", "")
    if ref_allowlist and not token:
        logger.warning("TEGAMI_GITHUB_TOKEN is unset: body_ref works for public repos only")

    return Config(
        allowed_recipients=allowed,
        ref_allowlist=ref_allowlist,
        github_token=token,
        max_body_bytes=int(env.get("TEGAMI_MAX_BODY_BYTES", "262144")),
        ref_timeout_s=float(env.get("TEGAMI_REF_TIMEOUT_S", "10")),
    )
