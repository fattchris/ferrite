# Ferrite Plugin — Prime Agent

Two pieces:

1. **MCP tools (live graph queries)** — registered in `~/.prime/agent/settings.json`:
   ```json
   "mcpServers": {
     "ferrite": {
       "type": "http",
       "url": "http://100.90.179.92:8002/mcp/",
       "bearerTokenEnvVar": "FERRITE_API_KEY"
     }
   }
   ```
   Equivalent one-liner: `prime-agent mcp add ferrite --url http://100.90.179.92:8002/mcp/ --bearer-token-env-var FERRITE_API_KEY`.
   `FERRITE_API_KEY` must be exported in the shell profile (scoped key per agent).

2. **Session-end auto-ingest (this extension)** — copy `index.ts` to
   `~/.prime/agent/extensions/ferrite/index.ts`. It is the Prime Agent
   extension entry point: on session end it POSTs the transcript to Ferrite
   `/store`, giving every conversation graph provenance (stamped server-side
   from the scoped `FERRITE_API_KEY`).

   Behavior:
   - Fires on session end (`quit`, `new`, `resume`, `fork`); skips hot-reload.
   - Stores user/assistant turn pairs as episodes (8000-char cap per field).
   - Skips sessions under `FERRITE_MIN_CHARS` total content chars (default 200).
   - Also exports `createFerritePlugin()` for OMP/Pi-style plugin loaders.

   | Env var | Default | Purpose |
   |---|---|---|
   | `FERRITE_API_KEY` | — | scoped key (required; create via `POST /keys`) |
   | `FERRITE_URL` | `http://100.90.179.92:8001` | REST endpoint (tailnet hub) |
   | `FERRITE_AUTO_INGEST` | `true` | `"false"` disables auto-ingest |
   | `FERRITE_MIN_CHARS` | `200` | skip trivial sessions |
   | `FERRITE_DEBUG` | off | `1` logs to `~/.ferrite/ingest-debug.log` |

Self-install: agents fetch `GET http://100.90.179.92:8001/self-install` for
the full walkthrough (key registration via 1Password, verification steps).
