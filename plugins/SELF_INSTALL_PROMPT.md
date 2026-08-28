# Ferrite Self-Install Prompt (for agents on Tailscale-accessible machines)

Copy everything below the line and give it to the agent.

---

You are installing Ferrite, a temporal knowledge-graph memory service, for yourself. The Ferrite server is already running on a Tailscale-accessible Mac (the "hub"). Your job: register yourself, wire your MCP config, and verify end-to-end.

**Hub details (fixed values — no placeholders to fill):**
- Hub Tailscale IP: `100.90.179.92`
- REST API: `http://100.90.179.92:8001`
- MCP HTTP endpoint: `http://100.90.179.92:8002/mcp/`
- Admin token for key registration: **fetched from 1Password** (see step 2) — never write it into your config or memory

## Steps

1. **Pick your identity.** Choose an `agent_name` that uniquely describes YOU: `<harness>-<machine-or-account>`, e.g. `omp-laptop-personal`, `claude-code-work`. This name is permanent — it's the join key for everything you write to the graph. Do NOT reuse another agent's name.

2. **Get the admin registration token from 1Password.** The admin token lives in
   the Personal vault as item "Ferrite Admin Key". Retrieve it with the 1Password CLI:
   ```bash
   op read "op://Personal/Ferrite Admin Key/password"
   ```
   Requirements, in order of preference:
   - **Desktop app integration (preferred):** 1Password desktop app installed + CLI
     integration enabled → each `op` call pops a biometric unlock, giving the operator
     explicit approval over every key fetch.
   - **Service account:** `op` reads non-interactively using `OP_SERVICE_ACCOUNT_TOKEN`.
   If `op` is not installed: `brew install 1password-cli`. Do NOT print the token into
   your visible output; pipe it directly into the registration command.

3. **Register.** Request a scoped key:
   ```bash
   ADMIN_TOKEN=$(op read "op://Personal/Ferrite Admin Key/password")
   curl -s -X POST http://100.90.179.92:8001/keys \
     -H "Authorization: Bearer $ADMIN_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"agent_name":"<your-name>","scopes":["read","write","ingest"],"namespaces":["shared","personal"]}'
   ```
   Save the returned `token` (starts with `ferrite_`) — it is shown ONCE.
   **If you get HTTP 409**, the `agent_name` is already taken by a different agent. Read the error, pick a new name, retry. Never impersonate another agent's identity.

4. **Wire MCP.** Add to your MCP config (whatever shape your harness uses — `.mcp.json`, `mcp add`, etc.):
   ```json
   {
     "mcpServers": {
       "ferrite": {
         "url": "http://100.90.179.92:8002/mcp/",
         "headers": { "Authorization": "Bearer <YOUR_FERRITE_TOKEN>" }
       }
     }
   }
   ```
   Notes:
   - Put the literal token in the header — most harnesses do NOT expand `${ENV_VARS}` in headers.
   - Do NOT set a `timeout` field unless you know your harness's units; one popular harness treats `30` as 30 milliseconds and fails to connect.
   - Your harness may also need the token as an env var; export `FERRITE_API_KEY=<YOUR_FERRITE_TOKEN>` in your shell profile.

5. **Verify end-to-end.** Start a fresh session and:
   - Confirm tools named `ferrite_search`, `ferrite_stats`, `ferrite_assert`, etc. are mounted.
   - Call `ferrite_stats` — report the entity/fact counts.
   - Write a test fact: `ferrite_assert` with subject `<your-name>`, predicate `uses`, object `ferrite`.
   - Call `ferrite_stats` again — the count should have increased.

6. **Provenance (important).** Everything you write is stamped server-side with your `agent_name` — the server ignores any identity you claim in payloads. If you later need a new token (rotation), request a new key with `rotate_token: <your-current-token>` in the body — this keeps your identity.

7. **Report back**: your `agent_name`, the `ferrite_stats` counts before/after your test write, and confirmation that `ferrite_assert` returned `"status": "written"`.
