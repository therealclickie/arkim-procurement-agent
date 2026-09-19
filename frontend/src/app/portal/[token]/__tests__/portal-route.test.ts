// @vitest-environment jsdom
/**
 * Structural coverage of the PUBLIC route wrapper `src/app/portal/[token]/page.tsx`
 * (review finding 1; gate I2 option (a)).
 *
 * React 18.3.1 (a dependency — untouchable per the prime directive) lacks the
 * `use` export the wrapper needs, so the wrapper cannot RENDER under vitest
 * (Next substitutes vendored React 19 at build time). Module-level import is
 * safe — only rendering crashes — so this file asserts the structural contract
 * the wrapper exists to provide. The behavioural surface (ClaimPage) is fully
 * covered in claim-page.test.tsx.
 */
import { describe, it, expect } from "vitest";
import PortalTokenPage, { dynamic, metadata } from "../page";
// Raw source of the wrapper, via Vite's ?raw query (import.meta.url is not a
// file-scheme URL under vitest, so node:fs + URL cannot be used here).
import SOURCE from "../page.tsx?raw";

describe("portal route wrapper — structural contract (/portal/[token]/page.tsx)", () => {
  it("default-exports a server component function", () => {
    expect(typeof PortalTokenPage).toBe("function");
  });

  it("is force-dynamic — a tokened public page must never be statically cached", () => {
    expect(dynamic).toBe("force-dynamic");
  });

  it("sets Referrer-Policy: no-referrer via the metadata export (no inline meta)", () => {
    expect(metadata.referrer).toBe("no-referrer");
    expect(metadata.title).toBe("Confirm your supplier profile");
  });

  it("unwraps params with use() and wires the token to ClaimPage inside Suspense", () => {
    // The one behaviour the wrapper adds. Scanned from source because the
    // render path cannot execute under vitest's React 18 (gate I2).
    expect(SOURCE).toMatch(/use\(params\)/);
    expect(SOURCE).toMatch(/<Suspense fallback=\{null\}>/);
    expect(SOURCE).toMatch(/<ClaimPage token=\{token\} \/>/);
    // No inline <meta> referrer element — the export is the single source.
    // (<meta followed by a non-'>' char, so prose mentions of "<meta>" in the
    // file's own doc comment don't trip it.)
    expect(SOURCE).not.toMatch(/<meta[^>]/i);
  });
});
