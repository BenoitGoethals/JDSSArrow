import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The FastAPI backend runs on :8000. Proxy API + WebSocket calls to it during dev so the
// SPA can use same-origin relative URLs.
//
// Quiet the transient proxy errors that happen when the backend restarts (e.g. "Apply &
// restart gateway") or is briefly offline while the browser still holds a /ws socket:
// EPIPE/ECONNRESET/ECONNREFUSED are expected there and shouldn't dump a stack trace.
const TRANSIENT = new Set(["EPIPE", "ECONNRESET", "ECONNREFUSED", "ETIMEDOUT"]);
const quiet = (proxy: any) => {
  proxy.on("error", (err: NodeJS.ErrnoException) => {
    if (TRANSIENT.has(err.code ?? "")) {
      console.warn(`[proxy] ${err.code} — backend on :8000 restarting or offline`);
    } else {
      console.warn("[proxy]", err.message);
    }
  });
};

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", configure: quiet },
      "/metrics": { target: "http://localhost:8000", configure: quiet },
      "/ws": { target: "ws://localhost:8000", ws: true, configure: quiet },
    },
  },
});
