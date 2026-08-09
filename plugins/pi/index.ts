/**
 * Ferrite Plugin for Pi (parent of OMP)
 * =====================================
 * Pi is TypeScript-based, same plugin model as OMP.
 * Shares the same MCP config and ingestion pattern.
 *
 * INSTALL (future):
 *   Copy to ~/.pi/plugins/ferrite/index.ts
 */

// STUB — same interface as OMP plugin, awaiting Pi plugin API docs.
// Pi source: https://github.com/garderlen/pi (or fork can1357/oh-my-pi)

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

// TODO: Same stubs as OMP — search, store, context, provenance, onSessionEnd

export default defaultConfig;
