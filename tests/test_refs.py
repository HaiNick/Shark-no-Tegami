import httpx
import pytest

from conftest import REF, SHA
from tegami.config import Config
from tegami.pipeline import Request, process
from tegami.refs import parse_ref


def transport(status=200, content=b"# Hello\n", seen=None, chunks=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if chunks is not None:
            return httpx.Response(status, stream=httpx.ByteStream(b"".join(chunks)))
        return httpx.Response(status, content=content)

    return httpx.MockTransport(handler)


async def run(cfg, relay, sender, ref=REF, tp=None, **kw):
    req = Request(to="01nicklas07@gmail.com", subject="s", body_ref=ref, content_type=kw.pop("content_type", "markdown"), dry_run=True, **kw)
    return await process(req, cfg, relay, transport=tp or transport(), sender=sender)


async def test_valid_ref_fetches_fixed_host_with_headers(cfg, relay, sender):
    seen = []
    r = await run(cfg, relay, sender, tp=transport(seen=seen))
    assert r["status"] == "validated"
    assert r["source"] == REF
    assert len(r["source_sha256"]) == 64
    req = seen[0]
    assert str(req.url) == f"https://api.github.com/repos/HaiNick/shimbun-archive/contents/digests/2026-W44.md?ref={SHA}"
    assert req.headers["Accept"] == "application/vnd.github.raw+json"
    assert req.headers["Authorization"] == "Bearer test-token"
    assert req.headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert sender.calls == []


@pytest.mark.parametrize(
    "ref,code",
    [
        ("HaiNick/shimbun-archive@0123abc:digests/x.md", "E_REF_FORMAT"),  # short SHA
        (f"HaiNick/shimbun-archive@{SHA}:digests/../secrets.md", "E_REF_FORMAT"),
        (f"HaiNick/shimbun-archive@{SHA}:/digests/x.md", "E_REF_FORMAT"),
        (f"HaiNick/shimbun-archive@{SHA}:digests/x.py", "E_REF_FORMAT"),
        ("https://evil.example/x.md", "E_REF_FORMAT"),
        (f"HaiNick/shimbun-archive@{SHA}:other/x.md", "E_REF_NOT_ALLOWED"),
        (f"HaiNick/shimbun-archive@{SHA}:digests-old/x.md", "E_REF_NOT_ALLOWED"),
        (f"HaiNick/other-repo@{SHA}:digests/x.md", "E_REF_NOT_ALLOWED"),
    ],
)
async def test_bad_refs_never_fetch(cfg, relay, sender, ref, code):
    seen = []
    r = await run(cfg, relay, sender, ref=ref, tp=transport(seen=seen))
    assert r["status"] == "error" and r["code"] == code, r
    assert seen == []


async def test_no_allowlist_rejects_every_ref(relay, sender):
    r = await run(Config(), relay, sender)
    assert r["code"] == "E_REF_NOT_ALLOWED"


def test_repo_match_is_case_insensitive(cfg):
    assert parse_ref(f"hainick/Shimbun-Archive@{SHA}:digests/a.md", cfg).path == "digests/a.md"


async def test_github_404(cfg, relay, sender):
    r = await run(cfg, relay, sender, tp=transport(status=404, content=b"Not Found"))
    assert r["code"] == "E_REF_FETCH" and "404" in r["message"]


async def test_github_timeout(cfg, relay, sender):
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    r = await run(cfg, relay, sender, tp=httpx.MockTransport(handler))
    assert r["code"] == "E_REF_FETCH" and "timed out" in r["message"]


async def test_oversized_response_declared(cfg, relay, sender):
    r = await run(cfg, relay, sender, tp=transport(content=b"a" * (cfg.max_body_bytes + 1)))
    assert r["code"] == "E_TOO_LARGE"


async def test_oversized_response_streamed_without_length(cfg, relay, sender):
    # No Content-Length: the streamed read has to stop on its own.
    r = await run(cfg, relay, sender, tp=transport(chunks=[b"a" * 1024] * 8))
    assert r["code"] == "E_TOO_LARGE" and "exceeds" in r["message"]


async def test_non_utf8_bytes(cfg, relay, sender):
    r = await run(cfg, relay, sender, tp=transport(content=b"caf\xe9\n"))
    assert r["code"] == "E_REF_FETCH" and "UTF-8" in r["message"]
