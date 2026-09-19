import { defineConfig } from "vitest/config";
import path from "node:path";

// Unit tests (node environment, React-free composition logic, brief §7.9) plus
// component tests (jsdom, per-file `// @vitest-environment jsdom` pragma — the
// frontend test floor). Default environment stays "node" so the original unit
// tests are unchanged.
export default defineConfig({
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  // Vite 8 (rolldown/oxc) does not transform JSX in .tsx test files unless the
  // JSX runtime is configured — Next's SWC does this for the app build, but
  // vitest never goes through SWC. automatic runtime → react/jsx-runtime, the
  // same transform the app's tsconfig ("jsx": "preserve" for Next) defers.
  oxc: { jsx: { runtime: "automatic" } },
  test: {
    include: ["src/**/*.test.{ts,tsx}"],
    environment: "node",
    setupFiles: ["./vitest.setup.ts"],
  },
});
