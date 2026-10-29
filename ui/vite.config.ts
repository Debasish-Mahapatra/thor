import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `make dev` runs the backend on 8790 with the development token; Vite forwards the API to it.
const backend = "http://127.0.0.1:8790";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: { "/api": backend, "/auth": backend },
  },
  // PDF.js is large; this app loads everything from the local machine, so chunk size does not matter.
  build: { outDir: "dist", emptyOutDir: true, chunkSizeWarningLimit: 4000 },
});
