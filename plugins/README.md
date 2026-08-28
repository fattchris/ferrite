# Ferrite Plugin Stubs

Plugin integrations for each AI agent harness to connect to Ferrite.

## Status

| Plugin | Status |
|--------|--------|
| Hermes | ✅ **FUNCTIONAL** — installed at ~/.hermes/plugins/ferrite/ (5 tools + ferrite_assert, pre-inference recall, backfill driver, auto-heal) |
| Claude Code | 🟡 MCP server ready (stdio + HTTP :8002/mcp/); settings.json stub needs updating to new mcp.json shape |
| Codex | 🟡 MCP server ready; config.toml stub needs updating |
| OMP | 🔴 stub — TS plugin API TBD |
| Pi | 🔴 stub — same as OMP |

Auth model: every agent gets its OWN scoped API key (POST /keys, admin-gated)
with a distinct agent_name. Provenance is stamped server-side from the key —
clients cannot forge identity. Two agents of the same harness = two keys.

These are interface stubs for later implementation. Ferrite server
doesn't exist yet either. These define the integration contract.

## Layout

```
plugins/
├── hermes/
│   └── ferrite_plugin.py      # Hermes + OpenClaw (shared Python plugin)
├── claude-code/
│   └── settings.json          # MCP config + SessionEnd hook
├── codex/
│   ├── config.toml            # MCP server config
│   └── ferrite-wrapper.sh     # Session wrapper (no hooks available)
├── omp/
│   ├── .mcp.json              # MCP server config
│   └── index.ts               # TS plugin stub (hindsight API TBD)
└── pi/
    ├── .mcp.json              # MCP server config
    └── index.ts               # TS plugin stub (same as OMP)
```

## Integration Matrix

| Harness     | MCP Tools | Auto-Ingest     | Mechanism              |
|-------------|-----------|-----------------|------------------------|
| Hermes      | ✅ Plugin | ✅ on_session_end| Python plugin          |
| OpenClaw    | ✅ Shared | ✅ on_session_end| Same plugin, diff cfg  |
| Claude Code | ✅ .mcp   | ✅ SessionEnd   | settings.json + curl   |
| Codex CLI   | ✅ config | ⚠️ Wrapper      | config.toml + wrapper  |
| OMP         | ✅ .mcp   | ⚠️ Hindsight API| .mcp.json + TS plugin  |
| Pi          | ✅ .mcp   | ⚠️ Same as OMP  | .mcp.json + TS plugin  |

## Environment Variables

All plugins expect:
- `FERRITE_ENDPOINT` — Ferrite MCP server URL (default: http://localhost:8000)
- `FERRITE_API_KEY` — Bearer token for auth
- `FERRITE_NAMESPACE` — Agent namespace for isolation (default: "default")
- `FERRITE_AUTO_INGEST` — Enable session auto-ingestion (default: "true")

## TODO

- [ ] Implement Hermes plugin registration (awaiting Ferrite server)
- [ ] Test Claude Code SessionEnd hook
- [ ] Investigate OMP plugin API (TypeScript)
- [ ] Build Codex wrapper script test
- [ ] Verify Pi plugin model matches OMP
