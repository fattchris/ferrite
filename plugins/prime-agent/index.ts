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
  apiKeyEnv: "FERRITE_API_KEY",
  autoIngest: process.env.FERRITE_AUTO_INGEST !== "false",
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
