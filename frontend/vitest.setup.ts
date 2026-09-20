/**
 * Shared test setup (runs for every test file, node and jsdom alike).
 *
 * RTL's auto-cleanup relies on a global afterEach hook, which vitest does not
 * expose with `globals: false` — register it here once instead of per-file.
 * Unstubbing globals and restoring mocks after each test keeps the fetch-seam
 * stubs (the test floor's single network seam) from leaking between files.
 */
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
