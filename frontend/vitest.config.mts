import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

// The pure parts only: no browser, no DOM, no React rendering. `vitest
// run` opens no server.
export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL(".", import.meta.url)) },
  },
  test: {
    environment: "node",
    include: ["lib/**/*.test.ts", "tests/**/*.test.ts"],
  },
});
