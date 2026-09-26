// @vitest-environment jsdom
/**
 * PH-01 round 3c, finding 1 — the gofer run page's spec panel decided "Confirm &
 * Source" itself (manufacturer || part_number). It now reads the backend's
 * run.intake_readiness, fails closed when it is missing, and a reasoned 422 shows
 * the backend's message instead of "is the backend running?".
 */
import { describe, it, expect } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { jsonResponse, stubFetch } from "@/test-support/fetch-seam";
import { SpecPanel } from "../intake/spec-panel";
import type { SourcingRunDetail } from "@/types";

const CONFIRM = /confirm (&|and) source/i;

function run(
  asset_specs: Record<string, unknown>,
  intake_readiness?: SourcingRunDetail["intake_readiness"],
  phase = "intake",
): SourcingRunDetail {
  return {
    id: "run-3c", phase, asset_specs, intake_readiness,
    approval_history: [],
  } as unknown as SourcingRunDetail;
}

function renderPanel(r: SourcingRunDetail) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SpecPanel run={r} />
    </QueryClientProvider>,
  );
}

const NOT_READY_MFG = { ready: false, missing_attrs: ["manufacturer"], missing_labels: ["manufacturer"] };
const READY = { ready: true, missing_attrs: [], missing_labels: [] };

describe("SpecPanel readiness", () => {
  it("manufacturer only: the backend says not ready, so no Confirm & Source", () => {
    // The old rule (manufacturer || part_number) offered confirm here.
    renderPanel(run(
      { manufacturer: "Ashcroft", detected_type: "pressure gauge", manufacturer_confidence: 90 },
      { ready: false, missing_attrs: ["model"], missing_labels: ["model or part number"] },
    ));
    expect(screen.queryByRole("button", { name: CONFIRM })).toBeNull();
    const needed = screen.getByTestId("spec-panel-still-needed");
    expect(needed.textContent).toContain("model or part number");
  });

  it("part number only: not ready, no confirm", () => {
    renderPanel(run({ part_number: "1032-XYZ", manufacturer_confidence: 0 }, NOT_READY_MFG));
    expect(screen.queryByRole("button", { name: CONFIRM })).toBeNull();
  });

  it("fails closed when the run detail carries no readiness", () => {
    renderPanel(run({ manufacturer: "Ashcroft", model: "1032", part_number: "1032-XYZ",
                      manufacturer_confidence: 95 }));
    expect(screen.queryByRole("button", { name: CONFIRM })).toBeNull();
  });

  it("offers Confirm & Source when the backend says ready", () => {
    renderPanel(run({ manufacturer: "Ashcroft", model: "1032", manufacturer_confidence: 95 }, READY));
    const button = screen.getByRole("button", { name: CONFIRM }) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
  });

  it("follows the backend even where the specs alone look incomplete", () => {
    renderPanel(run({ model: "1032", manufacturer_confidence: 0 }, READY));
    expect(screen.getByRole("button", { name: CONFIRM })).toBeTruthy();
  });

  it("never offers confirm outside intake", () => {
    renderPanel(run({ manufacturer: "Ashcroft", model: "1032", manufacturer_confidence: 95 },
                    READY, "comparison"));
    expect(screen.queryByRole("button", { name: CONFIRM })).toBeNull();
  });

  it("a reasoned 422 shows the backend's message, not a connectivity error", async () => {
    stubFetch((call) =>
      call.url.includes("/confirm-intake")
        ? jsonResponse(422, { detail: {
            message: "This is hygienic service, so the part has to match the skid: what wetted material?",
            reason: "hygienic_spec_incomplete", override: "source_anyway" } })
        : jsonResponse(200, {}),
    );
    renderPanel(run({ manufacturer: "Ashcroft", model: "1032", manufacturer_confidence: 95 }, READY));
    fireEvent.click(screen.getByRole("button", { name: CONFIRM }));
    await waitFor(() => expect(screen.getByText(/what wetted material\?/)).toBeTruthy());
    expect(screen.queryByText(/is the backend running/)).toBeNull();
  });
});
