import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

// The global environment stays "node" for the existing plain-TypeScript logic
// tests (e.g. subdomain/slug parsing). Component tests opt into "jsdom"
// per-file via a `// @vitest-environment jsdom` pragma comment at the top of
// the test file instead of switching this globally.
export default defineConfig({
  resolve: {
    alias: {
      // Mirror the `"@/*": ["./src/*"]` path alias from tsconfig.json.
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    setupFiles: ["./vitest.setup.ts"],
  },
});
