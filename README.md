# Shark-no-Tegami

Shark-no-Tegami is a [FastMCP](https://github.com/jlowin/fastmcp) server that exposes a single `send_email` tool. It relays plain-text, HTML, and Markdown email through a Postfix instance reachable at a configurable SMTP host (typically over Tailscale), and is designed for use with Claude Code Routines for automated digest and alert emails.

Reference implementation: [Shark-no-Kari](https://github.com/HaiNick/Shark-no-Kari)

---

## Tool: `send_email`

| Parameter | Type | Required | Description |
|---|---|---|---|
| `to` | string | **yes** | Recipient address or comma-separated list. Every address must be in `TEGAMI_ALLOWED_RECIPIENTS` if that variable is set. |
| `subject` | string | **yes** | Subject line. Max 200 characters, no CR or LF. |
| `body` | string | one of | Inline body. Pass exactly one of `body` and `body_ref`. |
| `body_ref` | string | one of | `OWNER/REPO@SHA40:PATH`: a `.md`, `.html`, or `.txt` file at a full commit SHA, fetched from the GitHub contents API. The repo and path prefix must be in `TEGAMI_REF_ALLOWLIST`. |
| `content_type` | string | no (default `plain`) | `plain`, `html`, or `markdown` |
| `theme` | string | no | Only with `markdown`: `default` (default) or `shimbun` |
| `cc` | string | no | CC addresses, comma-separated. Same allowlist check. |
| `bcc` | string | no | BCC addresses, comma-separated. Passed to the relay, never added to headers. Same allowlist check. |
| `dry_run` | bool | no | Resolve, validate, and render, then return stats without sending. |

### Processing

1. Check arguments and recipients.
2. Resolve the body (inline, or `body_ref` from `https://api.github.com` with a streamed read capped at `TEGAMI_MAX_BODY_BYTES`, strict UTF-8, SHA-256 of the bytes).
3. Render:
   - `plain`: `text/plain` only.
   - `html`: the content validator runs on the body. Any violation returns `E_UNSAFE_CONTENT`.
   - `markdown` + `default`: CommonMark with raw HTML off, `nh3` cleaning, a trusted 640 px template, inlined CSS, then the validator.
   - `markdown` + `shimbun`: the strict Shark-no-Shimbun dialect parser and renderer vendored from the `shimbun-digest` skill (`src/tegami/vendor/shimbun_lib.py`), then the validator with the template relaxations.
4. Send `multipart/alternative` (text part plus HTML part) with `Message-ID` and `Date`.
5. Log one metadata line: timestamp, recipient count, source, SHA-256, HTML bytes, and result code. Bodies and subjects are never logged.

The validator (`src/tegami/validate.py`) parses the HTML with Lexbor and rejects scripting and embedding elements, `on*` handlers, URL attributes with schemes other than `http`, `https`, and `mailto`, inline styles with `url(`, `expression(`, `@import`, or script schemes, `srcdoc`, `http-equiv`, and MSO conditional comments.

### Results

Success:

```json
{"status": "sent", "message_id": "<...>", "source": "inline", "source_sha256": "...", "html_bytes": 38211, "items": {"Security": 7}}
```

`items` appears only for `theme: shimbun`. With `dry_run`, `status` is `validated`, `message_id` is absent, and `html_preview` holds the first 500 characters of the HTML.

Errors come back as results, not exceptions:

```json
{"status": "error", "code": "E_UNSAFE_CONTENT", "message": "2 violations", "details": ["..."]}
```

| Code | When |
|---|---|
| `E_ARGS` | Both or neither of `body`/`body_ref`; bad `content_type` or `theme`; `theme` without `markdown`; CR or LF in subject, or subject over 200 characters; malformed address |
| `E_RECIPIENT` | Any address not on the allowlist (`details` lists them) |
| `E_REF_FORMAT` | `body_ref` doesn't match the pattern, or the path is absolute or has `.` or `..` segments |
| `E_REF_NOT_ALLOWED` | Repo or path prefix not allowlisted, or `TEGAMI_REF_ALLOWLIST` unset |
| `E_REF_FETCH` | GitHub non-200, timeout, or content that isn't valid UTF-8 |
| `E_TOO_LARGE` | Body over `TEGAMI_MAX_BODY_BYTES` |
| `E_UNSAFE_CONTENT` | Validator found violations (`details` lists them) |
| `E_THEME_PARSE` | Markdown doesn't match the Shimbun dialect; the message starts with `line N:` |
| `E_SMTP` | Relay refused or timed out |

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `MAIL_FROM` | **yes** | | Envelope sender address |
| `SMTP_HOST` | no | `localhost` | Postfix relay hostname |
| `SMTP_PORT` | no | `25` | SMTP port (plain, no TLS) |
| `OIDC_ENABLED` | no | | Set `true` to enable OIDC auth (mutually exclusive with `MCP_API_KEY`) |
| `OIDC_CONFIG_URL` | if OIDC | | OIDC discovery URL |
| `OIDC_CLIENT_ID` | if OIDC | | OIDC client ID |
| `OIDC_CLIENT_SECRET` | if OIDC | | OIDC client secret (optional) |
| `OIDC_BASE_URL` | if OIDC | | Public base URL of this server |
| `JWT_SIGNING_KEY` | if OIDC | | Key used to sign session JWTs |
| `MCP_API_KEY` | no | | Bearer token for simple auth (mutually exclusive with `OIDC_ENABLED`) |
| `TEGAMI_ALLOWED_RECIPIENTS` | no | | Comma-separated recipient allowlist (case-insensitive). If unset, any recipient is accepted and the server logs a warning at startup. |
| `TEGAMI_REF_ALLOWLIST` | no | | Comma-separated `OWNER/REPO:PATH_PREFIX` pairs. Prefixes match whole path segments. If unset, every `body_ref` fails with `E_REF_NOT_ALLOWED`. |
| `TEGAMI_GITHUB_TOKEN` | for private repos | | Fine-grained PAT with **Contents: Read-only** on the allowlisted repos |
| `TEGAMI_MAX_BODY_BYTES` | no | `262144` | Byte limit for inline and fetched bodies |
| `TEGAMI_REF_TIMEOUT_S` | no | `10` | Timeout for the GitHub request, in seconds |

---

## Tests

```bash
pip install -r requirements.txt
pytest
```

---

## Deployment

### docker-compose.yml

```yaml
services:
  shark-no-tegami:
    image: ghcr.io/hainick/shark-no-tegami:latest
    restart: unless-stopped
    environment:
      MAIL_FROM: alerts@example.com
      SMTP_HOST: postfix  # Tailscale hostname or service name
      SMTP_PORT: "25"
      OIDC_ENABLED: "true"
      OIDC_CONFIG_URL: https://sso.example.com/.well-known/openid-configuration
      OIDC_CLIENT_ID: shark-no-tegami
      OIDC_BASE_URL: https://tegami.example.com
      JWT_SIGNING_KEY: ${JWT_SIGNING_KEY}
    ports:
      - "8000:8000"
```

### Caddyfile

```
tegami.example.com {
    reverse_proxy localhost:8000
}
```
