/**
 * Ferrite Plugin for Prime Agent
 * ======================================
 * Bridges OMP/Pi hindsight memory events + session lifecycle to Ferrite.
 *
 * Features:
 *  1. MCP tools come from .mcp.json (ferrite server at :8002/mcp/) — this
 *     plugin handles what MCP cannot: session lifecycle + auto-ingest.
 *  2. On session end, POSTs the transcript to Ferrite /store so every
 *     conversation lands in the knowledge graph with correct provenance.
 *  3. Provenance is stamped server-side from the scoped FERRITE_API_KEY.
 *
 * Env:
 *   FERRITE_API_KEY  scoped key (required) — create via POST /keys
 *   FERRITE_URL      default http://localhost:8001
 *   FERRITE_AUTO_INGEST  "false" disables session-end ingest (default on)
 *
 * Install (Prime Agent):
 *   ~/.prime/agent/extensions/ferrite/index.ts
 *   MCP tools come from ~/.prime/agent/settings.json mcpServers.ferrite (bearerTokenEnvVar).
 *   Set FERRITE_API_KEY in the shell profile; this extension adds session-end ingest.
 */

export interface FerritePluginConfig {
  ferriteUrl?: string;
  apiKeyEnv?: string;
  autoIngest?: boolean;
  namespace?: string;
}

const DEFAULTS = {
  ferriteUrl: process.env.FERRITE_URL ?? "http://localhost:8001",
  apiKeyEnv: "FERRITE_API_KEY",
  autoIngest: process.env.FERRITE_AUTO_INGEST !== "false",
  namespace: "shared",
};

interface EpisodeMessage {
  role: string;
  content: string;
  assistant?: string;
  ts?: string;
}

async function storeTurn(
  cfg: typeof DEFAULTS,
  sessionId: string,
  msg: EpisodeMessage,
): Promise<{ episode_id?: string; status?: string } | null> {
  const key = process.env[cfg.apiKeyEnv];
  if (!key) {
    console.warn("[ferrite] no FERRITE_API_KEY — skipping store");
    return null;
  }
  try {
    const res = await fetch(`${cfg.ferriteUrl}/store`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${key}`,
      },
      body: JSON.stringify({
        content: JSON.stringify([msg]),
        content_type: "json",
        source: { kind: "session-sync", harness: "omp", session_id: sessionId },
        namespace: cfg.namespace,
      }),
    });
    if (!res.ok) {
      console.warn(`[ferrite] store failed: ${res.status}`);
      return null;
    }
    return (await res.json()) as { episode_id?: string; status?: string };
  } catch (e) {
    console.warn(`[ferrite] store error: ${e}`);
    return null;
  }
}

/** Public API expected by OMP/Pi plugin loader (contract per stub docs). */
export function createFerritePlugin(userCfg: FerritePluginConfig = {}) {
  const cfg = { ...DEFAULTS, ...userCfg };

  return {
    name: "ferrite",

    /** Called by harness after each exchange (turn-level hook if available). */
    async onTurn(sessionId: string, user: string, assistant: string, ts?: string) {
      if (!cfg.autoIngest) return;
      return storeTurn(cfg, sessionId, {
        role: "user",
        content: user.slice(0, 8000),
        assistant: assistant.slice(0, 8000),
        ts,
      });
    },

    /** Called on session end with the full transcript. */
    async onSessionEnd(sessionId: string, transcript: EpisodeMessage[]) {
      if (!cfg.autoIngest) return { stored: 0 };
      let stored = 0;
      for (let i = 0; i < transcript.length; i += 2) {
        const pair = transcript.slice(i, i + 2);
        const user = pair.find((m) => m.role === "user")?.content ?? "";
        const asst = pair.find((m) => m.role === "assistant")?.content ?? "";
        const r = await storeTurn(cfg, sessionId, {
          role: "user",
          content: user.slice(0, 8000),
          assistant: asst.slice(0, 8000),
          ts: pair[0]?.ts,
        });
        if (r) stored++;
      }
      return { stored };
    },

    /** Health check: ferrite /health endpoint. */
    async health(): Promise<boolean> {
      try {
        const res = await fetch(`${cfg.ferriteUrl}/health`);
        return res.ok;
      } catch {
        return false;
      }
    },
  };
}

export default createFerritePlugin;
