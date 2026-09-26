/**
 * PH-01 round 3b/3c — source scan: no frontend code has a readiness rule of its own.
 *
 * CLEANUP 5.7 was a client-side `specsReady` in request-screen.tsx that re-derived
 * readiness from the specs (manufacturer + model/PN, or spec_based_sourcing). Round 3
 * review finding 1 found a second one in the gofer spec panel (`hasSpecs` =
 * manufacturer || part_number → a live "Confirm & Source"). Round 3c widens this scan
 * from request-screen.tsx to EVERY file under frontend/src: readiness comes from the
 * backend's run.intake_readiness and nothing else, and any component that offers a
 * confirm reads it, failing closed when it is missing.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";

import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "..", "..", "..");

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) {
      if (name === "__tests__" || name === "test-support") continue;
      out.push(...sourceFiles(full));
    } else if (/\.(ts|tsx)$/.test(name) && !/\.test\.(ts|tsx)$/.test(name)) {
      out.push(full);
    }
  }
  return out;
}

const FILES = sourceFiles(SRC).map((f) => ({
  path: relative(SRC, f).split(sep).join("/"),
  text: readFileSync(f, "utf8"),
}));

/** The confirm API's own definitions — they offer nothing on their own. */
const CONFIRM_DEFINITIONS = new Set(["lib/api.ts", "lib/queries.ts"]);

/** The ONE shape a component may derive `ready` in: the backend field, fail-closed. */
const BACKEND_READY = /const\s+ready\s*=\s*run\??\.intake_readiness\?\.ready\s*===\s*true;/;

describe("frontend/src readiness (every file)", () => {
  it("scans the whole tree, including both intake surfaces", () => {
    const paths = FILES.map((f) => f.path);
    expect(paths).toContain("components/proc/request-screen.tsx");
    expect(paths).toContain("components/gofer/intake/spec-panel.tsx");
    expect(FILES.length).toBeGreaterThan(50);
  });

  it("has no client-side readiness function or spec-derived readiness flag", () => {
    const hits = FILES.filter((f) =>
      /specsReady|hasSpecs|function\s+\w*[Rr]eady\w*\s*\(/.test(f.text),
    ).map((f) => f.path);
    expect(hits).toEqual([]);
  });

  it("derives every `ready` from run.intake_readiness only", () => {
    const bad: string[] = [];
    for (const f of FILES) {
      for (const a of f.text.match(/const\s+ready\s*=\s*[^;]+;/g) ?? []) {
        if (!BACKEND_READY.test(a)) bad.push(`${f.path}: ${a}`);
      }
    }
    expect(bad).toEqual([]);
  });

  it("every component that offers a confirm reads the backend readiness", () => {
    const offering = FILES.filter(
      (f) => !CONFIRM_DEFINITIONS.has(f.path) && /\b(useConfirmIntake|confirmIntake)\s*\(/.test(f.text),
    );
    expect(offering.map((f) => f.path).sort()).toEqual([
      "components/gofer/intake/spec-panel.tsx",
      "components/proc/request-screen.tsx",
    ]);
    for (const f of offering) expect(f.text, f.path).toMatch(BACKEND_READY);
  });
});

describe("request-screen.tsx readiness", () => {
  const SOURCE = readFileSync(join(__dirname, "..", "request-screen.tsx"), "utf8");

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
