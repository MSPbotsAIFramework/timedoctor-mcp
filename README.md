# timedoctor-mcp

MCP server that connects [Time Doctor](https://www.timedoctor.com/) to the MSPbots Agent
platform. It exposes read-only reporting on a Time Doctor account: companies/workspaces,
team members, projects, raw time-tracking entries (worklogs), and aggregated productivity
activity (summary totals and a per-app/website category breakdown).

- **Transport**: MCP Streamable HTTP (`POST /mcp`), stateless.
- **Stack**: Python 3.12, `uv`, the MCP Python SDK's `mcp.server.fastmcp.FastMCP`.
- **License**: Apache-2.0. Copyright MSPbots.ai.

---

<div align="center">

## MSPbots Connector

**The MCP Gateway built for MSPs: visible, controlled, and your data stays where it belongs.**

[**Get started at connect.mspbots.ai →**](https://connect.mspbots.ai)

</div>

---

MSPbots Connector gives your AI clients one MCP URL to reach your entire MSP stack (RMM, PSA, documentation, security, finance), and gives you control over who can call which tool and a record of everything that was called.

**Hosted service: [connect.mspbots.ai](https://connect.mspbots.ai)** · Free to use.

### Why Connector

![MSPbots Connector](https://stgoss.mspbots.ai/files/mcp/mspbots-mcp.png)

| | |
|---|---|
| **Visible** | Every tool call is logged: who, which vendor, when. Usage dashboard, CSV export, SIEM streaming. |
| **Controlled** | Role-based, per-tool allowlist. Deny by default. |
| **Protected** | Credentials encrypted per record. AI clients never see your vendor passwords or tokens. |
| **MSP-native** | Built for the MSP stack only: RMM, PSA, documentation, security, finance. |

### Quick start

1. **Sign up** at [connect.mspbots.ai](https://connect.mspbots.ai).
2. **Connect your vendors.** Pick from 150+ connectors and authorize each one on the vendor's own page, or bring your own API keys. No extra vendor accounts needed.
3. **Set access.** Create roles and decide which tools each role may call. Nothing is allowed until you allow it.
4. **Add your MCP URL to your AI client.** Copy the URL from your Connector dashboard into Claude, ChatGPT, Cursor, VS Code, Gemini, Codex, or any MCP-capable client.
5. **Ask your AI to do real work**, and watch every call appear in the audit log.

### Features

**Unified gateway**
- One MCP URL for all your AI clients
- 150+ connectors across the MSP stack
- Custom MCP servers supported, free
- Authorization happens on each vendor's own page; the AI never sees passwords or tokens
- Bring your own credentials: use the vendor API keys you already have
- Multiple accounts per app; connect once, reuse across AI clients
- Tool search: finds the right action for the task
- One sign-in; vendor credentials are injected behind the scenes and OAuth tokens refresh automatically

**Visibility**
- Audit log for every tool call (who, which vendor, when)
- Usage dashboard: call volume, top vendors, error rate, per-user activity
- Export audit logs to CSV, or stream to Loki, Graylog, or LogScale (SIEM)
- Optional recording of tool arguments and result summaries (off by default, enabled per organization)

**Control**
- Per-role, per-tool allowlist, deny by default
- owner / admin / member roles
- SCIM provisioning with Entra ID, Okta, JumpCloud, and Google Workspace

**Security**
- Credentials encrypted one record at a time (AES-256-GCM, per-record key)
- AI clients authenticate with OAuth 2.1 + PKCE
- Vendor responses are scanned for prompt-injection patterns and returned as structured data. This is one layer of defense, not a guarantee against every attack.
- Static egress IP for customer firewall and API allowlisting

### Pricing

**Free.**

Links: [connect.mspbots.ai](https://connect.mspbots.ai) · [mspbots.ai](https://mspbots.ai)

---

## Authentication headers

| Header | Required | Meaning |
|---|---|---|
| `X-Timedoctor-Token` | yes | A Time Doctor v2 JWT. Obtain by calling `POST https://api2.timedoctor.com/api/1.0/login` with the account's `email`/`password` (and `totpCode` if two-factor is enabled); the response's `data.token` is valid for 6 months. Send it as the header value (no `JWT `/`Bearer ` prefix -- the server adds that upstream). |

The token is read fresh from this header on every request and is never stored, logged, or
cached. Each request must carry its own token; there is no server-side session. Tokens are
not auto-refreshed -- rotate the header value in your connector configuration before the
6-month expiry.

## Tools

All tools are read-only (`readOnlyHint: true`) and idempotent (`idempotentHint: true`).

| Tool | Parameters | Returns |
|---|---|---|
| `timedoctor_list_companies` | — | `[{id, name}]` |
| `timedoctor_list_users` | `company_id?`, `filter?`, `page? (default 1)`, `limit? (default 50, max 200)` | `{items:[{id, name, role}], page, limit, has_more}` |
| `timedoctor_list_projects` | `company_id?`, `page?`, `limit?` | `{items:[{id, name, status}], page, limit, has_more}` |
| `timedoctor_list_worklogs` | `company_id?`, `from_date?`, `to_date?`, `user_id?`, `page?`, `limit?` | `{items:[{user_id, task_id, project_id, start, end, duration_s}], page, limit, has_more}` |
| `timedoctor_get_activity_summary` | `company_id?`, `from_date?`, `to_date?`, `user_id?` | `{from, to, worked_s, manual_s, productive_s, unproductive_s}` |
| `timedoctor_get_activity_by_category` | `company_id?`, `from_date?`, `to_date?`, `user_id?` | `{from, to, categories:[{name, seconds}]}` |

Notes:

- `company_id` is optional everywhere. If omitted and the token can see exactly one company,
  that company is used automatically. If the token can see more than one, the call returns an
  `invalid_argument` error listing the candidate company ids/names instead of guessing.
- `from_date`/`to_date` (ISO `YYYY-MM-DD`) default to the trailing 7 days (UTC) when omitted.
- `limit` defaults to 50 and is capped at 200 regardless of what is requested.
- Errors are returned as a JSON envelope: `{"error":{"code","message","retryable"}}`, with
  `code` one of `not_configured` / `unauthorized` / `not_found` / `invalid_argument` /
  `rate_limited` / `upstream_error`.

## Local development

```bash
uv sync
uv run pytest
uv run python -m timedoctor_mcp   # starts on 0.0.0.0:8080
```

```bash
curl -X POST http://localhost:8080/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'X-Timedoctor-Token: <your JWT>' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

curl http://localhost:8080/health
```

## Docker

```bash
docker build --platform linux/amd64 -t timedoctor-mcp:dev .
docker run --rm -p 8080:8080 timedoctor-mcp:dev
```
