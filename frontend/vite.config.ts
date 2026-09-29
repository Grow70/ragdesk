import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Server-side environment only; never expose database/model credentials via VITE_*.
const target = process.env.API_PROXY_TARGET ?? "http://127.0.0.1:8000";
const proxy = {
  "/api": {
    target,
    changeOrigin: false,
    rewrite: (path: string) => path.replace(/^\/api(?=\/|$)/, ""),
  },
};

export default defineConfig({
  plugins: [react()],
  server: { host: "127.0.0.1", port: 5173, strictPort: true, proxy },
  preview: { host: "127.0.0.1", port: 4173, strictPort: true, proxy },
});
