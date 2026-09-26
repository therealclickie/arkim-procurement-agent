// @vitest-environment jsdom
/**
 * PH-01 round 3c — a model-only run shows NO enabled confirm action anywhere in the
 * frontend. Both surfaces that can confirm intake (the /request item card and the
 * /runs/[id] spec panel — the source scan in request-screen-no-client-readiness
 * pins that list) are rendered with the same model-only run detail, and every
 * enabled button is checked against the confirm / find-options / source vocabulary.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { jsonResponse, stubFetch } from "@/test-support/fetch-seam";
import { RequestScreen } from "../request-screen";
import { SpecPanel } from "@/components/gofer/intake/spec-panel";
import type { SourcingRunDetail } from "@/types";

const RUN = "run-model-only";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(`resume=${RUN}`),
  usePathname: () => "/request",
}));

vi.mock("../proc-shell", () => ({ useProcToast: () => vi.fn() }));

const MODEL_ONLY = {
  id: RUN,
  phase: "intake",
  asset_specs: { model: "1032", detected_type: "pressure gauge", manufacturer_confidence: 0 },
  intake_readiness: { ready: false, missing_attrs: ["manufacturer"], missing_labels: ["manufacturer"] },
  approval_history: [],
};

const CONFIRM_ACTION = /confirm|find options|source/i;

function enabledConfirmActions(): HTMLButtonElement[] {
  return (screen.queryAllByRole("button") as HTMLButtonElement[]).filter(
    (b) => !b.disabled && CONFIRM_ACTION.test(b.textContent ?? ""),
  );
}

function withClient(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

describe("a model-only run offers no enabled confirm action", () => {
  it("on the /request item card", async () => {
    stubFetch((call) =>
      call.url.endsWith(`/api/runs/${RUN}`) ? jsonResponse(200, MODEL_ONLY) : jsonResponse(200, {}),
    );
    withClient(<RequestScreen />);
    await screen.findByText("Need a little more");
    const findOptions = await screen.findByRole("button", { name: /find options/i });
    expect((findOptions as HTMLButtonElement).disabled).toBe(true);
    expect(enabledConfirmActions()).toEqual([]);
  });

  it("on the /runs/[id] spec panel", () => {
    withClient(<SpecPanel run={MODEL_ONLY as unknown as SourcingRunDetail} />);
    expect(screen.getByTestId("spec-panel-still-needed").textContent).toContain("manufacturer");
    expect(enabledConfirmActions()).toEqual([]);
  });
});
