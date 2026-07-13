# Shark-no-Tegami

Shark-no-Tegami is a [FastMCP](https://github.com/jlowin/fastmcp) server that exposes a single `send_email` tool. It relays plain-text email through a Postfix instance reachable at a configurable SMTP host — typically over Tailscale — and is designed for use with Claude Code Routines for automated digest and alert emails.

Reference implementation: [Shark-no-Kari](https://github.com/HaiNick/Shark-no-Kari)

---

## Tool: `send_email`

| Parameter | Type | Required | Description |
|---|---|---|---|
| `to` | string | **yes** | Recipient address or comma-separated list |
| `subject` | string | **yes** | Email subject line |
| `body` | string | **yes** | Plain-text or HTML body |
| `content_type` | string | no (default `plain`) | `plain` or `html` |
| `cc` | string | no | CC addresses, comma-separated |
| `bcc` | string | no | BCC addresses, comma-separated — passed to the SMTP relay but never added to message headers |

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `MAIL_FROM` | **yes** | — | Envelope sender address |
| `SMTP_HOST` | no | `localhost` | Postfix relay hostname |
| `SMTP_PORT` | no | `25` | SMTP port (plain, no TLS) |
| `OIDC_ENABLED` | no | — | Set `true` to enable OIDC auth (mutually exclusive with `MCP_API_KEY`) |
| `OIDC_CONFIG_URL` | if OIDC | — | OIDC discovery URL |
| `OIDC_CLIENT_ID` | if OIDC | — | OIDC client ID |
| `OIDC_CLIENT_SECRET` | if OIDC | — | OIDC client secret (optional) |
| `OIDC_BASE_URL` | if OIDC | — | Public base URL of this server |
| `JWT_SIGNING_KEY` | if OIDC | — | Key used to sign session JWTs |
| `MCP_API_KEY` | no | — | Bearer token for simple auth (mutually exclusive with `OIDC_ENABLED`) |

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
