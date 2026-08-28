/**
 * Ferrite Plugin for Prime Agent
 * ======================================
 * Turn-level bridge to Ferrite: every completed turn is POSTed to
 * Ferrite /store in real time, so facts become queryable by other
 * agents within minutes — long sessions share knowledge as they run.
 * Provenance is stamped server-side from the scoped FERRITE_API_KEY.
 *
 * MCP tools (live graph queries) come from ~/.prime/agent/settings.json
 * mcpServers.ferrite — see README.md in this directory.
 *
 * Env:
 *   FERRITE_API_KEY     scoped key (required) — create via POST /keys
 *   FERRITE_URL         default http://100.90.179.92:8001 (tailnet hub)
 *   FERRITE_AUTO_INGEST "false" disables session-end ingest (default on)
 *   FERRITE_MIN_CHARS   skip sessions shorter than this (default 200)
 *   FERRITE_DEBUG       "1" logs each ingest to ~/.ferrite/ingest-debug.log
 *   FERRITE_PREINJECT   "false" disables pre-inference recall (default on)
 *   FERRITE_INJECT_LIMIT   max facts injected per prompt (default 5)
 *   FERRITE_INJECT_MIN_SCORE  min relevance score (default 0 = server decides)
 *   FERRITE_MCP_URL     default http://100.90.179.92:8002/mcp/
 *
 * Install (Prime Agent):
 *   ~/.prime/agent/extensions/ferrite/index.ts
 *   (this file is the extension entry point; auto-discovered, no flags needed)
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export interface FerritePluginConfig {
  ferriteUrl?: string;
  apiKeyEnv?: string;
  autoIngest?: boolean;
  namespace?: string;
  minChars?: number;
}

const DEFAULTS = {
  ferriteUrl: process.env.FERRITE_URL ?? "http://100.90.179.92:8001",
  mcpUrl: process.env.FERRITE_MCP_URL ?? "http://100.90.179.92:8002/mcp/",
  apiKeyEnv: "FERRITE_API_KEY",
  autoIngest: process.env.FERRITE_AUTO_INGEST !== "false",
  preInject: process.env.FERRITE_PREINJECT !== "false",
  injectLimit: Number(process.env.FERRITE_INJECT_LIMIT ?? 5) || 5,
  injectMinScore: Number(process.env.FERRITE_INJECT_MIN_SCORE ?? 0),
  namespace: "shared",
  minChars: Number(process.env.FERRITE_MIN_CHARS ?? 10) || 10,
};

interface EpisodeMessage {
  role: string;
  content: string;
  assistant?: string;
  ts?: string;
}

function debugLog(message: string): void {
  if (process.env.FERRITE_DEBUG !== "1") return;
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const fs = require("fs");
    fs.appendFileSync(
      `${process.env.HOME}/.ferrite/ingest-debug.log`,
      `${new Date().toISOString()} ${message}\n`,
    );
  } catch {
    /* logging is best-effort */
  }
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
        source: { kind: "session-sync", harness: "prime-agent", session_id: sessionId },
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

/** Factory also usable by OMP/Pi-style plugin loaders. */
export function createFerritePlugin(userCfg: FerritePluginConfig = {}) {
  const cfg = { ...DEFAULTS, ...userCfg };

  return {
    name: "ferrite",

    /** Called by harness after each exchange (turn-level hook if available). */
    async onTurn(sessionId: string, user: string, assistant: string, ts?: string) {
      if (!cfg.autoIngest) return;
      if ((user.length + assistant.length) < cfg.minChars) {
        debugLog(`onTurn session=${sessionId} skipped: ${user.length + assistant.length} chars < min ${cfg.minChars}`);
        return null;
      }
      const r = await storeTurn(cfg, sessionId, {
        role: "user",
        content: user.slice(0, 8000),
        assistant: assistant.slice(0, 8000),
        ts,
      });
      debugLog(`onTurn session=${sessionId} user=${user.length}ch assistant=${assistant.length}ch -> ${r ? "stored" : "skipped/failed"}`);
      return r;
    },

    /** Called on session end with the full transcript. */
    async onSessionEnd(sessionId: string, transcript: EpisodeMessage[]) {
      if (!cfg.autoIngest) return { stored: 0 };
      const contentLen = transcript.reduce((n, m) => n + (m.content?.length ?? 0), 0);
      if (contentLen < cfg.minChars) {
        debugLog(`onSessionEnd session=${sessionId} skipped: ${contentLen} chars < min ${cfg.minChars}`);
        return { stored: 0 };
      }
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
      debugLog(`onSessionEnd session=${sessionId} turns=${transcript.length} stored=${stored}`);
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

function textOf(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter((b: any) => b?.type === "text" && typeof b.text === "string")
    .map((b: any) => b.text)
    .join("\n");
}

// ── Pre-inference recall ────────────────────────────────────────
// Before the agent loop runs, search Ferrite with the user's prompt and
// inject relevant facts into the conversation — the LLM starts with
// context instead of spending tool calls rediscovering it.

let mcpSessionId: string | null = null;

async function mcpCall(method: string, params?: any, retry = true): Promise<any> {
  const body: any = { jsonrpc: "2.0", id: Date.now(), method };
  if (params !== undefined) body.params = params;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json, text/event-stream",
  };
  const key = process.env[DEFAULTS.apiKeyEnv];
  if (key) headers.Authorization = `Bearer ${key}`;
  if (mcpSessionId) headers["mcp-session-id"] = mcpSessionId;

  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 5000);
  let res: Response;
  try {
    res = await fetch(DEFAULTS.mcpUrl, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
  } finally {
    clearTimeout(timer);
  }
  const sid = res.headers.get("mcp-session-id");
  if (sid) mcpSessionId = sid;
  if (res.status === 404 && retry) {
    mcpSessionId = null; // session expired — re-initialize once
    await mcpInit();
    return mcpCall(method, params, false);
  }
  if (!res.ok) throw new Error(`ferrite MCP HTTP ${res.status}`);

  const text = await res.text();
  if (method.startsWith("notifications/") || !text.trim()) return null;
  let payload: any = null;
  for (const line of text.split("\n")) {
    if (line.startsWith("data:")) payload = JSON.parse(line.slice(5).trim());
  }
  if (payload === null) payload = JSON.parse(text);
  if (payload?.error) throw new Error(payload.error.message ?? "ferrite MCP error");
  return payload;
}

async function mcpInit(): Promise<void> {
  const call = (m: string, p?: any) => mcpCall(m, p, false);
  await call("initialize", {
    protocolVersion: "2024-11-05",
    capabilities: {},
    clientInfo: { name: "ferrite-prime-agent-extension", version: "1.0" },
  });
  await call("notifications/initialized", {});
}

async function ferriteRecall(prompt: string): Promise<Array<{ statement: string; score: number }>> {
  if (!mcpSessionId) await mcpInit();
  const r = await mcpCall("tools/call", {
    name: "ferrite_inject",
    arguments: { text: prompt.slice(0, 2000) },
  });
  const raw = r?.result?.content?.[0]?.text;
  if (!raw) return [];
  const parsed = JSON.parse(raw);
  const results: any[] = Array.isArray(parsed?.results) ? parsed.results : [];
  return results
    .filter((x) => typeof x?.statement === "string" && x.score >= DEFAULTS.injectMinScore)
    .slice(0, DEFAULTS.injectLimit)
    .map((x) => ({ statement: x.statement, score: Number(x.score) || 0 }));
}

/**
 * Prime Agent extension entry point.
 *
 * Real-time ingestion: every completed turn with text is POSTed to Ferrite
 * /store immediately, so agents on other machines can query fresh facts
 * within minutes — no waiting for session end. The user prompt is read
 * from the session entries (robust across resume/reload) and held until a
 * turn produces assistant text, so each episode carries the prompt together
 * with its answer. Continuation turns (tool loops, follow-ups) are sent as
 * assistant-only episodes.
 */
export default function (pi: ExtensionAPI) {
  const plugin = createFerritePlugin({});

  pi.on("before_agent_start", async (event) => {
    if (!DEFAULTS.preInject) return;
    const prompt = (event.prompt ?? "").trim();
    if (prompt.length < 10) return;
    try {
      const facts = await ferriteRecall(prompt);
      if (facts.length === 0) return;
      debugLog(`recall prompt=${prompt.length}ch injected=${facts.length} facts`);
      return {
        message: {
          customType: "ferrite-recall",
          content:
            "[ferrite recall — facts auto-retrieved from the shared knowledge graph for this prompt]\n" +
            facts.map((f) => `- ${f.statement}`).join("\n") +
            "\n[Use as background context; verify before acting on anything critical.]",
          display: true,
        },
      };
    } catch (e: any) {
      debugLog(`recall failed: ${e?.message ?? e}`);
      return; // never block the prompt on recall failures
    }
  });

  let pendingUserText = "";
  let pendingUserTs: string | undefined;
  let lastSentUserEntryId: string | null = null;

  pi.on("turn_end", async (event, ctx) => {
    const assistantText = textOf(event?.message?.content).trim();

    // Newest user entry we have not ingested yet (by entry id).
    let userText = "";
    let userTs: string | undefined;
    let userEntryId: string | null = null;
    try {
      const entries = ctx.sessionManager.getEntries() as any[];
      for (let i = entries.length - 1; i >= 0; i--) {
        const msg = entries[i]?.message;
        if (msg?.role === "user") {
          const t = textOf(msg.content).trim();
          const id = entries[i]?.id ?? null;
          if (t && id !== null && id !== lastSentUserEntryId) {
            userText = t;
            userTs = entries[i].timestamp ? new Date(entries[i].timestamp).toISOString() : undefined;
            userEntryId = id;
          }
          break; // only the most recent user entry is relevant
        }
      }
    } catch {
      /* fall back to pendingUserText */
    }

    if (userText) {
      pendingUserText = userText;
      pendingUserTs = userTs;
    }

    // Tool-only turns carry no text yet — hold the prompt for a later turn
    // so each episode keeps the prompt together with its answer.
    if (!assistantText) return;

    const file = ctx.sessionManager.getSessionFile?.();
    const sessionId = file
      ? String(file).split("/").pop()?.replace(/\.jsonl$/, "") ?? "unknown"
      : "unknown";

    try {
      await plugin.onTurn(sessionId, pendingUserText, assistantText, pendingUserTs);
    } catch (e: any) {
      if (ctx.hasUI) ctx.ui.notify(`ferrite ingest error: ${e?.message ?? e}`, "error");
      return; // keep pending state so a later turn retries the prompt
    }

    if (userEntryId !== null) lastSentUserEntryId = userEntryId;
    pendingUserText = "";
    pendingUserTs = undefined;
  });
}
