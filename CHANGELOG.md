# Changelog

## [0.4.0] - 2026-10-29

### Themes as editable Jinja templates

- **Every theme is a folder of templates** (`theme.yaml`, `email.html.j2`, optional
  `email.txt.j2`, and `schema.json` for JSON themes). The Shimbun layout moved from
  the vendored `render_html()` into `themes/shimbun/email.html.j2`; its styles, legend
  labels, and footer moved into `theme.yaml`. The output is unchanged (parity test).
- **Host overrides without a rebuild.** The image ships themes at `/app/themes.default`;
  a read-only mount at `/app/themes` (`THEMES_DIR`) overrides them per file. Templates
  are re-read on every render, so edits apply on the next send.
- **Theme inputs:** `markdown` (front matter as `meta`, `## ` sections), `shimbun`
  (strict dialect), and `json` (new `content_type: json`, validated against the
  theme's `schema.json`). New example theme `alert`.
- **Per-theme `allowed_recipients`** narrows the global allowlist, never widens it.
- **Sandboxed rendering:** `ImmutableSandboxedEnvironment`, autoescape, `StrictUndefined`.
- **New error codes:** `E_THEME_TEMPLATE` (broken template, `theme.yaml`, or schema;
  names the file and line) and `E_DATA_INVALID` (JSON body fails its schema; names the
  JSON path). Unknown or malformed theme names return `E_ARGS`.
- **Startup check:** renders a sample digest through every `shimbun` theme and compiles
  the others. Failures are logged; the server stays up.
- **Validator hardening for templates:**
  - Runs before and after CSS inlining (the inliner removes `<link>` and `@import`).
  - The inliner never loads remote stylesheets and drops `<link>` and @-rules.
  - Trusted `<style>` blocks must pass the blocked-CSS check.
  - Conditional comment bodies are validated as HTML (plain and table-wrapped) and
    with a raw token scan for blocked tags, `on*=`, `srcdoc`, `http-equiv`, and
    `javascript:`, `vbscript:`, and `data:` schemes.
- **Fix: validator segfault.** Reading `.attributes` on comment nodes crashes
  selectolax 0.3.x depending on heap state. The validator now skips non-element
  nodes. This also affected 0.3.0 (comment nodes appear in every Shimbun email).
- **Tool description** now lists the error codes in its first paragraph, the only part
  FastMCP 4 shows to clients.
- `body_ref` accepts `.json`. New dependencies: `jinja2`, `jsonschema`. New env vars:
  `THEMES_DIR`, `THEMES_DEFAULT_DIR`.

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
  `<style>` elements are blocked in caller HTML and allowed only in Tegami's
  own trusted templates.
- **`dry_run`** validates and renders without sending.
- **Recipient allowlist** (`TEGAMI_ALLOWED_RECIPIENTS`) for `to`, `cc`, and `bcc`.
- **Subject checks**: CR or LF and subjects over 200 characters are rejected.
- **Messages are `multipart/alternative`** with `Message-ID` and `Date`.
- **Logging is metadata only**: one line per call; recipients and subject are
  no longer logged.
- **Uvicorn trusts proxy headers** (`proxy_headers=True`, `forwarded_allow_ips="*"`)
  behind Caddy. OIDC config is unchanged.
- **fastmcp pinned to `>=4.1.4,<5`** (the version this release was tested with).
  Deploying it upgrades a 3.x container to FastMCP 4.x against live Pocket ID;
  the `OIDCProxy` arguments are unchanged.
- **Healthcheck probes `/.well-known/oauth-protected-resource/mcp` first.** FastMCP
  4.x (which the old `fastmcp>=3.4.4` resolved to) serves RFC 9728 metadata at the
  path-suffixed URL, so a fresh build of 0.2.0 reported unhealthy. The bare path
  stays as a fallback for 3.x.
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
