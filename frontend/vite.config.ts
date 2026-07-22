import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // host: true binds 0.0.0.0, not just localhost - required for the dev
    // server to be reachable from outside its Docker container (see
    // docker/docker-compose.yml); harmless for native `npm run dev` too.
    host: true,
    port: 5173,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
  },
});
