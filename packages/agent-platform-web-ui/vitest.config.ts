import { defineConfig } from "vitest/config";
import path from "node:path";

// First test config for agent-platform-web-ui — addresses M40 from the codebase
// review. Kept narrow so the next contributor can add a UI test target
// (jsdom) or an integration target (testcontainers) without fighting
// the unit-test setup.
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/__tests__/**/*.test.ts", "worker/__tests__/**/*.test.ts"],
    coverage: {
      provider: "v8",
      reporter: ["text", "html"],
      include: ["src/**/*.ts"],
      exclude: ["src/**/__tests__/**", "src/**/*.d.ts"],
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
