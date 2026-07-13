# Changelog

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
