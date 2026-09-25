/**
 * PH-01 round 3b — source scan: the intake card has no readiness rule of its own.
 *
 * CLEANUP 5.7 was a client-side `specsReady` that re-derived readiness from the
 * specs (manufacturer + model/PN, or spec_based_sourcing). The card's `ready` must
 * come from the backend's run.intake_readiness and nothing else.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const SOURCE = readFileSync(join(__dirname, "..", "request-screen.tsx"), "utf8");

describe("request-screen.tsx readiness", () => {
  it("has no client-side readiness function", () => {
    expect(SOURCE).not.toMatch(/specsReady/);
    expect(SOURCE).not.toMatch(/function\s+\w*[Rr]eady\w*\s*\(/);
  });

  it("does not read spec_based_sourcing or the old 'Matching by category' line", () => {
    expect(SOURCE).not.toMatch(/spec_based_sourcing/);
    expect(SOURCE).not.toMatch(/Matching by category/);
  });

  it("derives the card's ready state from run.intake_readiness only", () => {
    const assignments = SOURCE.match(/const\s+ready\s*=\s*[^;]+;/g) ?? [];
    expect(assignments).toEqual(["const ready = run?.intake_readiness?.ready === true;"]);
  });
});
