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
| `body_ref` | string | one of | `OWNER/REPO@SHA40:PATH`: a `.md`, `.html`, `.txt`, or `.json` file at a full commit SHA, fetched from the GitHub contents API. The repo and path prefix must be in `TEGAMI_REF_ALLOWLIST`. |
| `content_type` | string | no (default `plain`) | `plain`, `html`, `markdown`, or `json` |
| `theme` | string | no | Theme folder name (`^[a-z0-9-]{1,32}$`) for `markdown` or `json`. `markdown` defaults to `default`; `json` requires a theme whose `input` is `json`. Shipped: `default`, `shimbun`, `alert`. See [Editing themes](#editing-themes). |
| `cc` | string | no | CC addresses, comma-separated. Same allowlist check. |
| `bcc` | string | no | BCC addresses, comma-separated. Passed to the relay, never added to headers. Same allowlist check. |
| `dry_run` | bool | no | Resolve, validate, and render, then return stats without sending. |

### Processing

1. Check arguments, resolve the theme, and check recipients (the global allowlist, narrowed by the theme's `allowed_recipients` if it sets one).
2. Resolve the body (inline, or `body_ref` from `https://api.github.com` with a streamed read capped at `TEGAMI_MAX_BODY_BYTES`, strict UTF-8, SHA-256 of the bytes).
3. Render:
   - `plain`: `text/plain` only.
   - `html`: the content validator runs on the body. Any violation returns `E_UNSAFE_CONTENT`.
   - `markdown` or `json`: the theme's input parser builds the template context, the theme's `email.html.j2` renders it, and the validator runs with the template rules before and after CSS inlining. See [Editing themes](#editing-themes).
4. Send `multipart/alternative` (text part plus HTML part) with `Message-ID` and `Date`.
5. Log one metadata line: timestamp, recipient count, source, SHA-256, HTML bytes, and result code. Bodies and subjects are never logged.

The validator (`src/tegami/validate.py`) parses the HTML with Lexbor and rejects scripting and embedding elements, `<link>`, `<style>`, `on*` handlers, URL attributes with schemes other than `http`, `https`, and `mailto`, inline styles with `url(`, `expression(`, `@import`, or script schemes, `srcdoc`, `http-equiv`, and MSO conditional comments.

Theme templates are trusted for exactly three things: MSO conditional comments, `<meta>` without `http-equiv`, and `<style>` blocks whose CSS passes the same blocked-CSS check as inline styles. Conditional comment bodies are validated twice, once parsed as HTML and once as a raw token scan, because Outlook renders markup that the parser drops.

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
| `E_ARGS` | Both or neither of `body`/`body_ref`; bad `content_type`; a theme name that doesn't match the pattern, an unknown theme (the message lists the available ones), or a `content_type` that doesn't match the theme's `input`; CR or LF in subject, or subject over 200 characters; malformed address |
| `E_RECIPIENT` | Any address not on the allowlist (`details` lists them) |
| `E_REF_FORMAT` | `body_ref` doesn't match the pattern, or the path is absolute or has `.` or `..` segments |
| `E_REF_NOT_ALLOWED` | Repo or path prefix not allowlisted, or `TEGAMI_REF_ALLOWLIST` unset |
| `E_REF_FETCH` | GitHub non-200, timeout, or content that isn't valid UTF-8 |
| `E_TOO_LARGE` | Body over `TEGAMI_MAX_BODY_BYTES` |
| `E_UNSAFE_CONTENT` | Validator found violations (`details` lists them) |
| `E_THEME_PARSE` | Markdown doesn't match the Shimbun dialect, or front matter is invalid YAML or not a mapping; the message starts with `line N:` |
| `E_DATA_INVALID` | A `json` body isn't a JSON object, or fails the theme's `schema.json`; the message starts with the JSON path (`details` lists up to 20 errors) |
| `E_THEME_TEMPLATE` | A theme's template has a syntax error, an undefined variable, or a sandbox violation, or its `theme.yaml` or `schema.json` is invalid; the message names the file and line |
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
| `THEMES_DIR` | no | `/app/themes` | Host override folder for themes (mount it read-only) |
| `THEMES_DEFAULT_DIR` | no | `/app/themes.default` | Built-in themes shipped in the image |

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
    volumes:
      - ./themes:/app/themes:ro  # theme overrides; see Editing themes
    ports:
      - "8000:8000"
```

### Caddyfile

```
tegami.example.com {
    reverse_proxy localhost:8000
}
```

---

## Editing themes

Each theme is a folder with a `theme.yaml` and an `email.html.j2` Jinja template. Python only parses input and builds the template context; all HTML lives in the templates.

```
themes/
├── default/    input: markdown   theme.yaml, email.html.j2
├── shimbun/    input: shimbun    theme.yaml, email.html.j2
└── alert/      input: json       theme.yaml, email.html.j2, email.txt.j2, schema.json
```

### Where the files live

- The image ships the built-in themes at `/app/themes.default/`.
- The host mounts its own folder read-only at `/app/themes` (`THEMES_DIR`). On the VPS, that's `/opt/Shark-no-Tegami/themes/`.
- Tegami looks up each file in `/app/themes` first, then in `/app/themes.default`. The lookup is per file: a host folder that only has `shimbun/email.html.j2` still uses the built-in `shimbun/theme.yaml`. Delete a host file to go back to the built-in version.
- A theme exists when both `theme.yaml` and `email.html.j2` are found, in either location.

Edits apply on the next send. Tegami re-reads and recompiles the templates on every render, so there's no restart and no cache to clear.

### Preview a change

Use `dry_run` to render without sending. The result has `html_bytes`, `items` (Shimbun), and `html_preview` (the first 500 characters):

```json
{"to": "you@example.com", "subject": "Preview", "body_ref": "OWNER/REPO@SHA40:digests/FILE.md", "content_type": "markdown", "theme": "shimbun", "dry_run": true}
```

To see the full layout, send it to yourself without `dry_run`. The `shimbun-digest` skill's `render.py --html` previews only the layout shipped in that skill; it doesn't see templates edited on the host.

### Safety rules for templates

- Autoescape is on for `.html.j2` templates. Print values as `{{ value }}`; never use `|safe`.
- Undefined variables are errors (`StrictUndefined`), and templates run in Jinja's immutable sandbox.
- The content validator runs on every rendered email, before and after CSS inlining. A template that adds `<script>`, an event handler, a `javascript:` URL, a `<link>` stylesheet, or blocked CSS returns `E_UNSAFE_CONTENT`, even though templates are trusted for conditional comments, `<meta>`, and `<style>`.
- Tegami never fetches anything a template references. Remote stylesheets are rejected, not loaded.
- A broken template returns `E_THEME_TEMPLATE` with the file and line. At startup, Tegami renders a sample digest through every `shimbun` theme and compiles every other theme; failures are logged as errors and the server stays up.

### Template context

Every theme gets `subject` (the email subject) and `theme` (the parsed `theme.yaml`).

**`input: shimbun`** (content_type `markdown`, strict Shark-no-Shimbun dialect):

| Variable | Content |
|---|---|
| `digest` | `title`, `issue`, `range`, `cutoff`, `tldr`, `errors` (list of strings) |
| `sections` | One per non-empty section, in order: `label`, `key`, `color`, `items` |
| `sections[].items[]` | `title`, `url`, `tldr`, `source`, `date`, `badges` (list of `{name, style, warn}`; `warn` is true for `patch now`, `actively exploited`, and `security release`) |
| `stats` | Every configured section, empty or not: `key`, `label`, `legend`, `stat`, `chip`, `color`, `chip_style`, `count` |
| `cutoff_display` | Cutoff as `YYYY-MM-DD HH:MM` |
| `footer` | `theme.yaml` `footer` with `{title}`, `{issue}`, and `{cutoff}` replaced by escaped values |

The Shimbun `theme.yaml` holds the section list (labels, colors, `legend`, `chip_style`), `badge_styles`, and `footer`. The `footer` is trusted HTML, like the template.

**`input: markdown`** (content_type `markdown`):

| Variable | Content |
|---|---|
| `meta` | Front matter as a mapping (empty if the body has none). Front matter exists only if line 1 is `---` and a closing `---` line follows. |
| `content` | The whole body (after front matter) as cleaned HTML |
| `intro` | Cleaned HTML of the text before the first level-2 heading |
| `sections` | One per level-2 (`## `) heading: `title` (plain text) and `html` (cleaned HTML up to the next level-2 heading) |

Markdown is rendered with raw HTML off and cleaned with `nh3`, so `content`, `intro`, and `sections[].html` print without escaping.

**`input: json`** (content_type `json`):

| Variable | Content |
|---|---|
| `data` | The JSON object from the body, after it passes `schema.json` |

### theme.yaml keys

| Key | Required | Description |
|---|---|---|
| `input` | yes | `markdown`, `shimbun`, or `json` |
| `inline_css` | no (default `false`) | Inline `<style>` rules into `style` attributes after rendering. `default` and `alert` set it; `shimbun` writes inline styles itself. |
| `allowed_recipients` | no | List of addresses. Narrows `TEGAMI_ALLOWED_RECIPIENTS` for this theme (intersection); it can never allow an address the global list doesn't. |

Any other keys are free for the template to use through `theme`.

### Plain-text part

By default, `markdown` and `shimbun` themes send the source text as the `text/plain` part, and `json` themes send a tag-stripped version of the HTML. A theme can ship an optional `email.txt.j2` (same context, autoescape off) to render its own text part, as `alert` does.

### Creating a new theme

1. Pick an `input` type:
   - `markdown` for free-form reports where front matter and `## ` sections are enough.
   - `json` for structured data with a fixed shape, such as alerts or triage results.
   - `shimbun` only for the Shimbun digest dialect.
2. On the host, create the folder. The name must match `^[a-z0-9-]{1,32}$`.

   ```bash
   mkdir -p /opt/Shark-no-Tegami/themes/report
   ```

3. Add `theme.yaml` with at least `input`, and `email.html.j2`. For `input: json`, also add `schema.json` (JSON Schema draft 2020-12). Copying a shipped theme is the fastest start:

   ```bash
   cp -r /opt/Shark-no-Tegami/themes/alert /opt/Shark-no-Tegami/themes/report
   ```

4. Test it with `dry_run`, then with a real send to yourself:

   ```json
   {"to": "you@example.com", "subject": "Report test", "content_type": "json", "theme": "report", "dry_run": true, "body": "{\"title\": \"t\", \"severity\": \"info\", \"summary\": \"s\", \"fields\": []}"}
   ```

No Python changes or restart are needed. Routines pass the folder name as `theme`.

