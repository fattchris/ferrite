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

2. **Real-time turn ingest (this extension)** — copy `index.ts` to
   `~/.prime/agent/extensions/ferrite/index.ts`. It is the Prime Agent
   extension entry point: every completed turn is POSTed to Ferrite `/store`
   immediately, so other agents can query fresh facts within minutes —
   no waiting for session end (important for long sessions). Provenance is
   stamped server-side from the scoped `FERRITE_API_KEY`.

   Behavior:
   - `turn_end` fires per turn (one LLM response + its tool calls); each
     becomes an episode right away (8000-char cap per field).
   - The user prompt is attached to the first turn that follows it;
     continuation turns (tool loops, follow-ups) are assistant-only.
   - Tool calls/results are not sent — only user/assistant text.
   - `FERRITE_MIN_CHARS` (default 10) skips episodes below that size.
   - Also exports `createFerritePlugin()` (`onTurn`/`onSessionEnd`/`health`)
     for OMP/Pi-style plugin loaders.

   | Env var | Default | Purpose |
   |---|---|---|
   | `FERRITE_API_KEY` | — | scoped key (required; create via `POST /keys`) |
   | `FERRITE_URL` | `http://100.90.179.92:8001` | REST endpoint (tailnet hub) |
   | `FERRITE_AUTO_INGEST` | `true` | `"false"` disables auto-ingest |
   | `FERRITE_MIN_CHARS` | `10` | skip tiny/noise episodes |
| `FERRITE_PREINJECT` | `true` | pre-inference recall via `ferrite_inject` |
| `FERRITE_INJECT_LIMIT` | `5` | max facts injected per prompt |
| `FERRITE_MCP_URL` | hub `:8002/mcp/` | MCP endpoint for recall |
   | `FERRITE_DEBUG` | off | `1` logs to `~/.ferrite/ingest-debug.log` |

3. **Pre-inference recall (this extension)** — on `before_agent_start` the
   extension calls the `ferrite_inject` MCP tool with your prompt and
   injects the returned facts as a session message *before the first LLM
   call* — same pattern as the Hermes plugin. The model starts with
   relevant context instead of spending tool calls rediscovering it.

   - fires on every user prompt (>= 10 chars); fails open (errors are
     logged and skipped — recall never blocks the prompt)
   - relevance decisions are server-side (`ferrite_inject`); the extension
     formats and injects up to `FERRITE_INJECT_LIMIT` facts
   - injected messages carry `customType: "ferrite-recall"`, visible in
     the session (`display: true`)
   - `FERRITE_PREINJECT=false` disables it entirely

Self-install: agents fetch `GET http://100.90.179.92:8001/self-install` for
the full walkthrough (key registration via 1Password, verification steps).
