// @vitest-environment jsdom
/**
 * PH-01 round 3d — the chat promises "Source anyway", so the run page's spec panel
 * offers it the way the request card does: the backend's message, the Still needed
 * list, an acknowledgement checkbox that must be ticked, then confirm-intake with
 * source_anyway=true.
 */
import { describe, it, expect } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { isPostTo, jsonResponse, stubFetch } from "@/test-support/fetch-seam";
import { SpecPanel } from "../intake/spec-panel";
import type { SourcingRunDetail } from "@/types";

const SOURCE_ANYWAY = /source anyway/i;
const MESSAGE =
  "This request has no manufacturer and model, and no manufacturer part number — there is nothing to match a supplier's listing against.";

function run(
  intake_readiness: SourcingRunDetail["intake_readiness"],
  phase = "intake",
): SourcingRunDetail {
  return {
    id: "run-3d", phase,
    asset_specs: { detected_type: "bearing", bore_diameter: "25mm", manufacturer_confidence: 0 },
    intake_readiness,
    approval_history: [],
  } as unknown as SourcingRunDetail;
}

const NOT_READY = {
  ready: false,
  missing_attrs: ["manufacturer", "model"],
  missing_labels: ["manufacturer", "model or part number"],
  message: MESSAGE,
  override: "source_anyway",
};

function renderPanel(r: SourcingRunDetail) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SpecPanel run={r} />
    </QueryClientProvider>,
  );
}

describe("SpecPanel Source anyway", () => {
  it("shows the backend's message and the Still needed list", () => {
    renderPanel(run(NOT_READY));
    const needed = screen.getByTestId("spec-panel-still-needed");
    expect(needed.textContent).toContain(MESSAGE);
    expect(needed.textContent).toContain("manufacturer");
    expect(needed.textContent).toContain("model or part number");
    expect(screen.getByRole("button", { name: SOURCE_ANYWAY })).toBeTruthy();
  });

  it("requires the acknowledgement checkbox before it sends anything", () => {
    const calls = stubFetch(() => jsonResponse(200, { run_id: "run-3d", phase: "sourcing" }));
    renderPanel(run(NOT_READY));
    const button = screen.getByRole("button", { name: SOURCE_ANYWAY }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    expect(calls.filter((c) => isPostTo(c, "/confirm-intake"))).toHaveLength(0);
  });

  it("sends the override once acknowledged", async () => {
    const calls = stubFetch(() => jsonResponse(200, { run_id: "run-3d", phase: "sourcing" }));
    renderPanel(run(NOT_READY));
    fireEvent.click(screen.getByRole("checkbox"));
    const button = screen.getByRole("button", { name: SOURCE_ANYWAY }) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    fireEvent.click(button);
    await waitFor(() =>
      expect(calls.filter((c) => isPostTo(c, "/confirm-intake"))).toHaveLength(1));
    const confirm = calls.find((c) => isPostTo(c, "/confirm-intake"))!;
    expect(confirm.url).toContain("/runs/run-3d/confirm-intake");
    expect(confirm.url).toContain("source_anyway=true");
  });

  it("is not offered when the backend advertises no override", () => {
    renderPanel(run({ ...NOT_READY, override: null }));
    expect(screen.queryByRole("button", { name: SOURCE_ANYWAY })).toBeNull();
  });

  it("is not offered when ready", () => {
    renderPanel(run({ ready: true, missing_attrs: [], missing_labels: [] }));
    expect(screen.queryByRole("button", { name: SOURCE_ANYWAY })).toBeNull();
  });

  it("is not offered outside intake", () => {
    renderPanel(run(NOT_READY, "sourcing"));
    expect(screen.queryByRole("button", { name: SOURCE_ANYWAY })).toBeNull();
  });
});
