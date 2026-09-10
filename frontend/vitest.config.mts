import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

// Kept deliberately minimal: this project currently only needs to unit-test
// plain TypeScript logic (e.g. subdomain/slug parsing). React component tests
// would additionally need `@vitejs/plugin-react` and the `jsdom` environment —
// add those when the first component test lands, not before.
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
  },
});
