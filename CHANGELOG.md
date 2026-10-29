# Changelog

## [0.3.0] - 2026-10-29

### Tegami v2: Markdown, `body_ref`, and content validation

- **`send_email` returns JSON.** Success is `{"status": "sent", ...}`; errors are
  `{"status": "error", "code": "E_...", ...}` results, not exceptions.
- **`body_ref`**: fetch the body from `OWNER/REPO@SHA40:PATH` through the GitHub
  contents API, restricted by `TEGAMI_REF_ALLOWLIST`, with a streamed size cap.
- **`content_type: markdown`** with `theme: default` (CommonMark, `nh3`, inlined
  CSS) or `theme: shimbun` (vendored `shimbun_lib.py` from the `shimbun-digest`
  skill, per-section `items` counts).
- **Content validator** (`src/tegami/validate.py`, Lexbor-based) runs on caller
  HTML and on every rendered message. Violations return `E_UNSAFE_CONTENT`.
- **`dry_run`** validates and renders without sending.
- **Recipient allowlist** (`TEGAMI_ALLOWED_RECIPIENTS`) for `to`, `cc`, and `bcc`.
- **Subject checks**: CR or LF and subjects over 200 characters are rejected.
- **Messages are `multipart/alternative`** with `Message-ID` and `Date`.
- **Logging is metadata only**: one line per call; recipients and subject are
  no longer logged.
- **Uvicorn trusts proxy headers** (`proxy_headers=True`, `forwarded_allow_ips="*"`)
  behind Caddy. OIDC config is unchanged.
- New dependencies: `httpx`, `nh3`, `markdown-it-py`, `selectolax` (0.3.x),
  `css-inline`, `pyyaml`. New env vars: `TEGAMI_ALLOWED_RECIPIENTS`,
  `TEGAMI_REF_ALLOWLIST`, `TEGAMI_GITHUB_TOKEN`, `TEGAMI_MAX_BODY_BYTES`,
  `TEGAMI_REF_TIMEOUT_S`.

## [0.2.0] - 2026-07-14

### Fix: Pocket ID 2.10 / FastMCP 3.x compatibility (2026-07-13 incident)

Pocket ID v2.10.0 migrated its OAuth layer to fosite, which rejects the RFC 8707
`resource` parameter that OIDCProxy was forwarding upstream. This caused all
authentication attempts to fail with an upstream rejection.

**Changes in this release:**

- **`forward_resource=False` on OIDCProxy** — suppresses the `resource` parameter
  that Pocket ID 2.10 (fosite) rejects. `verify_id_token=True` and
  `required_scopes=["openid"]` are unchanged.

- **Removed FileTreeStore-based `client_storage`** — Claude.ai registers via CIMD
  (client_id is a URL). FileTreeStore uses the client_id as a filesystem path
  component, crashing with a 500 on `/authorize` for URL-shaped keys. Fleet-wide
  decision: use FastMCP's default in-memory client storage. Trade-off: container
  restart requires re-authentication (acceptable for this workload). Removed deps:
  `py-key-value-aio`, `cryptography`. Removed env var: `STORAGE_ENCRYPTION_KEY`.

- **fastmcp pinned to `>=3.4.4`** — FastMCP 3.4.3 has an origin-check bug that
  returns 403 "Forbidden Origin" on the consent POST when behind a reverse proxy
  that rewrites the `Host` header (e.g. Caddy). 3.4.4 fixes this.

- **Healthcheck replaced** — was an authenticated POST to `/mcp`; now an
  unauthenticated GET to `/.well-known/oauth-protected-resource` (HTTP 200).
  Uses a Python one-liner since `python:3.14-slim` has no `curl`.

### VPS manual steps required

Remove from `docker-compose.yml`:
- The `tegami_oauth_state` (or `oauth_state`) volume mount under `shark-no-tegami`
- The top-level `volumes: oauth_state:` entry

Remove from `.env` on the VPS:
- `STORAGE_ENCRYPTION_KEY=...`
