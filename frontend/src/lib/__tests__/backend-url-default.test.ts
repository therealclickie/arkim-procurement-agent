/**
 * T9 / ruling R9 (evaluation finding F-02) — the frontend points at the backend
 * by default.
 *
 * What the evaluation verified offline
 * (`eval/e2e-flags-on:eval/e2e/evidence/verify/verify_offline_checks.json` →
 * `F-02.default`): `next.config.ts` defaulted `NEXT_PUBLIC_API_URL` to
 * `http://localhost:8000`, while uvicorn serves `api_server` on **8001**. The
 * only reason this machine worked was a git-ignored `frontend/.env.local`; on a
 * fresh clone every `/api/*` rewrite missed.
 *
 * The backend port is read out of the repo's own README so this test fails if the
 * two ever drift again — it is not two copies of the same literal.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { BACKEND_DEFAULT_URL } from "../../../next.config";

const REPO_ROOT = join(__dirname, "..", "..", "..", "..");

/** The port in `uvicorn api_server:app --reload --port NNNN`, from the README. */
function backendPortFromReadme(): string {
  const readme = readFileSync(join(REPO_ROOT, "README.md"), "utf8");
  const match = readme.match(/uvicorn\s+api_server:app[^\n]*--port\s+(\d+)/);
  expect(match, "README.md must document the uvicorn command").not.toBeNull();
  return match![1];
}

describe("the NEXT_PUBLIC_API_URL default", () => {
  it("matches the port the backend actually serves on", () => {
    expect(BACKEND_DEFAULT_URL).toBe(`http://localhost:${backendPortFromReadme()}`);
  });

  it("is no longer the 8000 the evaluation observed", () => {
    expect(BACKEND_DEFAULT_URL).not.toBe("http://localhost:8000");
  });

  it("is an absolute http origin with no trailing slash", () => {
    expect(BACKEND_DEFAULT_URL).toMatch(/^https?:\/\/[^/]+$/);
  });

  it("is documented in the frontend README", () => {
    const readme = readFileSync(join(REPO_ROOT, "frontend", "README.md"), "utf8");
    expect(readme).toContain(BACKEND_DEFAULT_URL);
    expect(readme).toContain("NEXT_PUBLIC_API_URL");
  });

  it("leaves no stale 8000 reference in the frontend README", () => {
    const readme = readFileSync(join(REPO_ROOT, "frontend", "README.md"), "utf8");
    expect(readme).not.toContain("localhost:8000");
  });
});
