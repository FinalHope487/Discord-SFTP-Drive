import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The bundle is served from the root of the aiohttp process that serves the
// API, so `base` stays "/" rather than "./". Relative asset URLs would resolve
// against the current client-side route -- a reload on /trash would look for
// /trash/assets/... and get the HTML shell back instead of JavaScript.
export default defineConfig({
  base: "/",
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    // What Electron 43 (Chromium 140) and every current browser run without a
    // transpile step. Nothing here has to work on a browser older than the
    // shell it ships inside.
    target: "es2022",
    // Off. A sourcemap of this bundle is a map of how the client talks to an
    // API that hands out file contents, and it would be served to anybody who
    // can reach the login page.
    sourcemap: false,
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      // `npm run dev` proxies to the real server, so the session cookie is
      // same-origin in development exactly as it is in production. Talking to
      // 8080 directly would need CORS that production does not have, and the
      // SameSite=Strict cookie would not be sent at all -- the bug would only
      // exist in the environment used to look for bugs.
      "/api": {
        target: "http://127.0.0.1:8080",
        changeOrigin: false,
      },
    },
  },
});
