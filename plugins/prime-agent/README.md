# Ferrite Plugin — Prime Agent

Two pieces:

1. **MCP tools (live graph queries)** — registered in `~/.prime/agent/settings.json`:
   ```json
   "mcpServers": {
     "ferrite": {
       "type": "http",
       "url": "http://localhost:8002/mcp/",
       "bearerTokenEnvVar": "FERRITE_API_KEY"
     }
   }
   ```
   `FERRITE_API_KEY` must be exported in the shell profile (scoped key per agent).

2. **Session-end auto-ingest (this extension)** — copy `index.ts` to
   `~/.prime/agent/extensions/ferrite/index.ts`. On session end it POSTs the
   transcript to Ferrite `/store`, giving every conversation graph provenance.

Self-install: agents fetch `GET http://100.90.179.92:8001/self-install` for
the full walkthrough (key registration via 1Password, verification steps).
