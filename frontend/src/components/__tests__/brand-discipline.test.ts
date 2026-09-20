/**
 * T8 — brand discipline: BRAND_NAME (src/lib/brand.ts) is the single source of
 * truth for the brand name. No application source under the public-facing
 * scan scope (src/app/portal, src/app/quote, src/components) may carry a
 * hard-coded title-case "Gofer" / "Arkim" STRING LITERAL — a rebrand should
 * be a one-line change in brand.ts, not a grep-and-replace across the tree.
 *
 * Scan definition (flagged for the reviewer): EXACT title-case word inside a
 * quoted string literal (", ', `), EXCLUDING comments (line + block, stripped
 * before matching), identifiers/component names (GoferLoader, useGoferStore —
 * never quoted), import paths, and lowercase storage keys. Test files,
 * test-support, and __tests__ dirs are outside the application source and
 * excluded. Bare JSX text is not covered by this scan (no such occurrence
 * exists in scope; the one known literal is an alt attribute).
 *
 * Backend mirror (per the brief): utils/supplier_portal.py::_ZERO_STATE_FRAMING
 * renders framing copy server-side. It currently says "Gofer", which MATCHES
 * BRAND_NAME = "Gofer" (the brief's "still contains Arkim" is stale). Recorded
 * as a brief-staleness note (F5) in the report — no finding, backend untouched.
 */
import { describe, it, expect } from "vitest";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..", ".."); // frontend/src
// The brief's scan scope: the public routes' sources + every shared component.
const SCOPE = [join(ROOT, "app", "portal"), join(ROOT, "app", "quote"), join(ROOT, "components")];

/** Recursively collect .ts/.tsx application sources (no tests, no support). */
function collect(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      if (entry.name === "__tests__" || entry.name === "test-support") continue;
      collect(join(dir, entry.name), out);
    } else if (/\.(ts|tsx)$/.test(entry.name) && !entry.name.includes(".test.")) {
      out.push(join(dir, entry.name));
    }
  }
  return out;
}

/** Drop block comments and indented line comments (URLs in strings survive).
 *  Newlines inside stripped comments are KEPT so line numbers stay true. */
function stripComments(src: string): string {
  const noBlocks = src.replace(/\/\*[\s\S]*?\*\//g, (c) => "\n".repeat((c.match(/\n/g) ?? []).length));
  return noBlocks
    .split("\n")
    .map((line) => line.replace(/\s\/\/.*$/, ""))
    .join("\n");
}

/** Quoted string literals containing an exact title-case Gofer/Arkim word. */
const LITERAL_WITH_BRAND = /(["'`])(?:(?!\1)[\s\S])*\b(Gofer|Arkim)\b(?:(?!\1)[\s\S])*\1/g;

/** 1-based line number of a character offset in the original source. */
function lineOf(src: string, offset: number): number {
  let line = 1;
  for (let i = 0; i < offset; i++) if (src.charCodeAt(i) === 10) line++;
  return line;
}

describe("T8 — brand discipline (BRAND_NAME is the single source of truth)", () => {
  it("no hard-coded 'Gofer'/'Arkim' string literal exists in the scan scope", () => {
    // FINDING (F1, fixed): src/components/proc/gofer-mark.tsx rendered the
    // brand mark's alt text as the literal alt="Gofer" — the one hard-coded
    // title-case brand string literal in the scan scope (src/app/portal,
    // src/app/quote, src/components), outside src/lib/brand.ts. BRAND_NAME
    // is the single source of truth; a rebrand would have missed that alt
    // text (and every screen reader user would hear the old name). The mark
    // now renders alt={BRAND_NAME}.
    const violations: string[] = [];
    for (const root of SCOPE) {
      for (const file of collect(root)) {
        const src = readFileSync(file, "utf8");
        // Match on comment-stripped text (newlines preserved so lines stay true).
        const stripped = stripComments(src);
        const re = new RegExp(LITERAL_WITH_BRAND.source, "g");
        let m: RegExpExecArray | null;
        while ((m = re.exec(stripped)) !== null) {
          const where = relative(join(ROOT, ".."), file);
          violations.push(`${where}:${lineOf(stripped, m.index)} — literal ${m[0]} (use BRAND_NAME)`);
        }
      }
    }
    expect(
      violations,
      `Hard-coded brand literals found (BRAND_NAME in src/lib/brand.ts is the source of truth):\n  ${violations.join("\n  ")}`,
    ).toEqual([]);
  });
});
