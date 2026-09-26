// @vitest-environment jsdom
/**
 * PH-01 round 3c, finding 2 — the results banner for a run sourced with unmet
 * requirements renders the backend's lines (intake_readiness.unverified_banner_lines)
 * on the proc options screen, which had no banner at all before this round.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { UnverifiedBanner } from "../unverified-banner";
import { unverifiedBannerLines } from "@/lib/unverified";
import type { SourcingResults } from "@/types";

// Arc 5's identity banner, verbatim (utils/intake_sufficiency.BANNER).
const IDENTITY =
  "These results have NOT been checked against your requirement — the request was " +
  "sourced without a manufacturer and model or a manufacturer part number.";
const HYGIENIC =
  "These results have NOT been checked against your requirement — the request was " +
  "sourced without confirming its wetted material and hygienic certification (3-A / EHEDG / none).";
const ALSO =
  "It was also sourced without confirming its wetted material and hygienic certification (3-A / EHEDG / none).";

const base = { tier1: [], tier2: [], tier3: [] };

describe("UnverifiedBanner", () => {
  it("a hygienic-override run shows the hygienic banner", () => {
    render(<UnverifiedBanner results={{ ...base, specIncomplete: true,
      specIncompleteBanner: HYGIENIC, specIncompleteBannerLines: [HYGIENIC],
      unverifiedRequirements: ["hygienic"] } as SourcingResults} />);
    const banner = screen.getByTestId("spec-incomplete-banner");
    expect(banner.textContent).toBe(HYGIENIC);
    expect(banner.textContent).not.toContain("manufacturer");
  });

  it("an identity-override run keeps arc 5's text exactly", () => {
    render(<UnverifiedBanner results={{ ...base, specIncomplete: true,
      specIncompleteBanner: IDENTITY } as SourcingResults} />);
    expect(screen.getByTestId("spec-incomplete-banner").textContent).toBe(IDENTITY);
  });

  it("a run overriding both names both", () => {
    render(<UnverifiedBanner results={{ ...base, specIncomplete: true,
      specIncompleteBanner: IDENTITY, specIncompleteBannerLines: [IDENTITY, ALSO],
      unverifiedRequirements: ["identity", "hygienic"] } as SourcingResults} />);
    const text = screen.getByTestId("spec-incomplete-banner").textContent ?? "";
    expect(text).toContain(IDENTITY);
    expect(text).toContain("hygienic certification");
  });

  it("a checked run shows nothing", () => {
    const { container } = render(<UnverifiedBanner results={base as SourcingResults} />);
    expect(container.textContent).toBe("");
    expect(unverifiedBannerLines(undefined)).toEqual([]);
  });

  it("is rendered by the options screen and the gofer sourcing view", () => {
    const options = readFileSync(join(__dirname, "..", "options-screen.tsx"), "utf8");
    expect(options).toMatch(/<UnverifiedBanner results=\{sr\} \/>/);
    const gofer = readFileSync(
      join(__dirname, "..", "..", "gofer", "sourcing", "sourcing-view.tsx"), "utf8");
    expect(gofer).toMatch(/unverifiedBannerLines\(results\)/);
  });
});
