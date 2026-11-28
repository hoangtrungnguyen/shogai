import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backend = process.env.SHOGAI_BACKEND_URL ?? "http://127.0.0.1:8000";
const page = (name: string) => decodeURIComponent(new URL(name, import.meta.url).pathname);

export default defineConfig({
  plugins: [react()],
  build: {
    rolldownOptions: {
      // index.html is the app; ask.html is the public page (served by the public listener).
      input: { main: page("./index.html"), ask: page("./ask.html") },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: backend, changeOrigin: true },
      "/llms.txt": { target: backend, changeOrigin: true },
    },
  },
});
