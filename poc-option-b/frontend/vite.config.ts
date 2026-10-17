import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The build is served by the controller (FastAPI) from controller/web/dist; `npm run dev` proxies the API.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: "/",
  build: { outDir: "../controller/web/dist", emptyOutDir: true, sourcemap: false, chunkSizeWarningLimit: 900 },
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8770", "/health": "http://127.0.0.1:8770" },
  },
});
