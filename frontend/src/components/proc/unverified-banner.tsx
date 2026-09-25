"use client";

import { unverifiedBannerLines } from "@/lib/unverified";
import type { SourcingResults } from "@/types";
import { ProcIcon } from "./proc-icon";

/** The options screen's "NOT checked against your requirement" banner (arc 5 / PH-01
 *  round 3c): what a Source anyway run was sourced without, in the backend's words.
 *  Renders nothing for a checked run. */
export function UnverifiedBanner({ results }: { results?: SourcingResults | null }) {
  const lines = unverifiedBannerLines(results);
  if (lines.length === 0) return null;
  return (
    <div className="proc-working" role="note" data-testid="spec-incomplete-banner">
      <ProcIcon name="alert" size={20} color="var(--st-overdue)" />
      <div>
        {lines.map((l, i) => (
          <div key={l} className={i === 0 ? "w-t" : "w-s"}>{l}</div>
        ))}
      </div>
    </div>
  );
}
