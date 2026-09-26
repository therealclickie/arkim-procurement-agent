import type { SourcingResults } from "@/types";

/**
 * PH-01 round 3c — the banner lines for a run sourced with unmet requirements
 * (identity and/or hygienic, via Source anyway). The backend derives them from ONE
 * value (intake_readiness.unverified_requirements) that also caps every badge below
 * exact; the UI only renders them. Empty for a checked run.
 */
export function unverifiedBannerLines(results?: SourcingResults | null): string[] {
  if (!results) return [];
  if (results.specIncompleteBannerLines?.length) return results.specIncompleteBannerLines;
  return results.specIncompleteBanner ? [results.specIncompleteBanner] : [];
}
