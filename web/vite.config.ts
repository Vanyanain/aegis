import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5310,
    // The console is API-driven; proxying in dev keeps fetch paths identical to production,
    // where FastAPI serves both the JSON and this bundle from one origin.
    proxy: { "/api": "http://127.0.0.1:8311" },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
