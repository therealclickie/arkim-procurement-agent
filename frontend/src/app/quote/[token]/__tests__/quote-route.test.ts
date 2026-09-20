// @vitest-environment jsdom
/**
 * Structural coverage of the PUBLIC route wrapper `src/app/quote/[token]/page.tsx`
 * (review finding 1; gate I2 option (a), applied to the quote surface).
 *
 * Same constraint as the portal wrapper: React 18.3.1 lacks `use`, so the
 * wrapper cannot RENDER under vitest (Next substitutes vendored React 19 at
 * build time), but module-level import is safe. The behavioural surface
 * (QuotePage) is fully covered in quote-page.test.tsx.
 */
import { describe, it, expect } from "vitest";
import QuoteTokenPage, { dynamic, metadata } from "../page";
// Raw source of the wrapper, via Vite's ?raw query (import.meta.url is not a
// file-scheme URL under vitest, so node:fs + URL cannot be used here).
import SOURCE from "../page.tsx?raw";

describe("quote route wrapper — structural contract (/quote/[token]/page.tsx)", () => {
  it("default-exports a server component function", () => {
    expect(typeof QuoteTokenPage).toBe("function");
  });

  it("is force-dynamic — a tokened public page must never be statically cached", () => {
    expect(dynamic).toBe("force-dynamic");
  });

  it("sets Referrer-Policy: no-referrer via the metadata export (no inline meta)", () => {
    expect(metadata.referrer).toBe("no-referrer");
    expect(metadata.title).toBe("Submit your quote");
  });

  it("unwraps params with use() and wires the token to QuotePage inside Suspense", () => {
    expect(SOURCE).toMatch(/use\(params\)/);
    expect(SOURCE).toMatch(/<Suspense fallback=\{null\}>/);
    expect(SOURCE).toMatch(/<QuotePage token=\{token\} \/>/);
    // No inline <meta> referrer element — the export is the single source.
    // (<meta followed by a non-'>' char, so prose mentions of "<meta>" in a
    // doc comment wouldn't trip it.)
    expect(SOURCE).not.toMatch(/<meta[^>]/i);
  });
});
