/**
 * Ferrite Plugin for OMP (Oh My Pi)
 * ================================
 * OMP is TypeScript-based. This stub defines the plugin interface
 * for Ferrite integration. OMP has "hindsight memory" — this plugin
 * bridges hindsight events to Ferrite's ingestion pipeline.
 *
 * TODO: Investigate OMP's plugin/extension API (TypeScript).
 * OMP source: https://github.com/can1357/oh-my-pi
 *
 * WHAT THIS PLUGIN WILL DO (when implemented):
 * 1. Register MCP tools from Ferrite (search, store, context, provenance)
 * 2. Hook into OMP's hindsight memory system
 * 3. On session end, POST transcript to Ferrite ingestion endpoint
 *
 * INSTALL (future):
 *   Copy to ~/.omp/agent/plugins/ferrite/index.ts
 *   Add to ~/.omp/agent/config.yml:
 *     plugins:
 *       - ferrite
 */

// STUB — not yet functional. Awaiting OMP plugin API investigation.

interface FerriteConfig {
  endpoint: string;
  apiKey: string;
  namespace: string;
  autoIngest: boolean;
}

const defaultConfig: FerriteConfig = {
  endpoint: process.env.FERRITE_ENDPOINT || "http://localhost:8000",
  apiKey: process.env.FERRITE_API_KEY || "",
  namespace: process.env.FERRITE_NAMESPACE || "default",
  autoIngest: process.env.FERRITE_AUTO_INGEST !== "false",
};

// TODO: Implement when OMP plugin API is documented
// export function register(config: Partial<FerriteConfig>) {
//   const cfg = { ...defaultConfig, ...config };
//   // Register MCP tools
//   // Hook into hindsight memory
//   // Register session lifecycle callback
// }

// TODO: Implement MCP tool wrappers
// export async function search(query: string, limit: number = 10) {
//   // POST to Ferrite MCP endpoint
// }

// export async function store(text: string, source: string) {
//   // POST to Ferrite MCP endpoint
// }

// TODO: Implement session ingestion hook
// export async function onSessionEnd(sessionId: string, transcript: any) {
//   // POST transcript to Ferrite ingestion endpoint
// }

export default defaultConfig;
