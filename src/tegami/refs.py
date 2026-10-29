"""Resolve body_ref (OWNER/REPO@SHA40:PATH) against the GitHub contents API."""

import hashlib
import re
from dataclasses import dataclass

import httpx

from .config import Config
from .errors import TegamiError

GITHUB_API = "https://api.github.com"  # fixed; never taken from the caller
REF_RE = re.compile(r"^([\w.-]+/[\w.-]+)@([0-9a-f]{40}):([\w./-]+\.(md|html|txt|json))$", re.ASCII)


@dataclass(frozen=True)
class Ref:
    repo: str
    sha: str
    path: str

    def __str__(self) -> str:
        return f"{self.repo}@{self.sha}:{self.path}"


@dataclass(frozen=True)
class Body:
    text: str
    sha256: str
    source: str


def parse_ref(value: str, cfg: Config) -> Ref:
    m = REF_RE.match(value)
    if not m:
        raise TegamiError("E_REF_FORMAT", "body_ref must be OWNER/REPO@SHA40:PATH ending in .md, .html, .txt, or .json")
    repo, sha, path = m.group(1), m.group(2), m.group(3)
    if path.startswith("/") or any(seg in ("", ".", "..") for seg in path.split("/")):
        raise TegamiError("E_REF_FORMAT", "body_ref path must not be absolute or contain empty, '.', or '..' segments")
    if not any(repo.lower() == r and path.startswith(p) for r, p in cfg.ref_allowlist):
        raise TegamiError("E_REF_NOT_ALLOWED", f"{repo}:{path} is not covered by TEGAMI_REF_ALLOWLIST")
    return Ref(repo, sha, path)


def body_from_bytes(data: bytes, source: str) -> Body:
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as e:
        raise TegamiError("E_REF_FETCH", f"content is not valid UTF-8 (byte {e.start})")
    return Body(text, hashlib.sha256(data).hexdigest(), source)


async def fetch_ref(ref: Ref, cfg: Config, transport: httpx.AsyncBaseTransport | None = None) -> Body:
    url = f"{GITHUB_API}/repos/{ref.repo}/contents/{ref.path}"
    headers = {
        "Accept": "application/vnd.github.raw+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if cfg.github_token:
        headers["Authorization"] = f"Bearer {cfg.github_token}"

    limit = cfg.max_body_bytes
    chunks: list[bytes] = []
    size = 0
    try:
        async with httpx.AsyncClient(transport=transport, timeout=cfg.ref_timeout_s, follow_redirects=False) as client:
            async with client.stream("GET", url, params={"ref": ref.sha}, headers=headers) as resp:
                if resp.status_code != 200:
                    raise TegamiError("E_REF_FETCH", f"GitHub returned HTTP {resp.status_code} for {ref}")
                declared = resp.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > limit:
                    raise TegamiError("E_TOO_LARGE", f"{ref} is {declared} bytes, limit is {limit}")
                # aiter_bytes yields decoded content incrementally, so the cap also stops compression bombs.
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise TegamiError("E_TOO_LARGE", f"{ref} exceeds the {limit}-byte limit")
                    chunks.append(chunk)
    except httpx.TimeoutException:
        raise TegamiError("E_REF_FETCH", f"GitHub request timed out after {cfg.ref_timeout_s:g} s")
    except httpx.HTTPError as e:
        raise TegamiError("E_REF_FETCH", f"GitHub request failed: {type(e).__name__}")

    return body_from_bytes(b"".join(chunks), str(ref))
