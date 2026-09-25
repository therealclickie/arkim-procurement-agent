// @vitest-environment jsdom
/**
 * PH-01 round 3b — the intake card renders readiness ONLY from the backend.
 *
 * CLEANUP 5.7: the card used to decide "Part identified" itself (manufacturer +
 * model/PN, or spec_based_sourcing), so it could enable Find options on specs that
 * confirm-intake refuses. It now reads run.intake_readiness (intake_readiness.assess
 * on the backend). These tests drive the real RequestScreen through the fetch seam
 * (a ?resume= deep link) with the run detail's readiness as the only variable.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { jsonResponse, stubFetch } from "@/test-support/fetch-seam";
import { RequestScreen } from "../request-screen";

const RUN = "run-ph01-3b";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(`resume=${RUN}`),
  usePathname: () => "/request",
}));

vi.mock("../proc-shell", () => ({ useProcToast: () => vi.fn() }));

const CERT = "hygienic certification (3-A / EHEDG / none)";

/** A backend whose run detail carries `asset_specs` and `intake_readiness`. */
function stubRun(asset_specs: Record<string, unknown>, intake_readiness?: unknown) {
  return stubFetch((call) => {
    if (call.url.endsWith(`/api/runs/${RUN}`)) {
      return jsonResponse(200, { id: RUN, phase: "intake", asset_specs, intake_readiness });
    }
    return jsonResponse(200, {});
  });
}

function renderScreen() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <RequestScreen />
    </QueryClientProvider>,
  );
}

async function findOptionsButton(): Promise<HTMLButtonElement> {
  return (await screen.findByRole("button", { name: /find options/i })) as HTMLButtonElement;
}

async function expectNotReady(labels: string[]) {
  const needed = await screen.findByTestId("readiness-still-needed");
  expect(needed.textContent).toContain("Still needed:");
  for (const l of labels) expect(needed.textContent).toContain(l);
  expect(screen.getByText("Need a little more")).toBeTruthy();
  expect(screen.queryByText("Part identified")).toBeNull();
  expect((await findOptionsButton()).disabled).toBe(true);
}

describe("a not-ready run", () => {
  it("model only: shows Still needed with the manufacturer and does not offer sourcing", async () => {
    stubRun(
      { model: "1032", detected_type: "pressure gauge" },
      { ready: false, missing_attrs: ["manufacturer"], missing_labels: ["manufacturer"] },
    );
    renderScreen();
    await expectNotReady(["manufacturer"]);
  });

  it("hygienic missing certification: identity-complete specs are still not ready", async () => {
    // Manufacturer + model: the old client-side rule called this "Part identified".
    stubRun(
      { manufacturer: "Ashcroft", model: "1009", detected_type: "pressure gauge",
        description: "Sanitary gauge for the CIP skid" },
      { ready: false, missing_attrs: ["hygienic_certification"], missing_labels: [CERT] },
    );
    renderScreen();
    await expectNotReady([CERT]);
  });

  it("spec_based_sourcing does not make it ready when the backend says not ready", async () => {
    stubRun(
      { detected_type: "pressure gauge", spec_based_sourcing: true },
      { ready: false, missing_attrs: ["manufacturer", "model"],
        missing_labels: ["manufacturer", "model or part number"] },
    );
    renderScreen();
    await expectNotReady(["manufacturer", "model or part number"]);
    expect(screen.queryByText(/Matching by category/)).toBeNull();
  });

  it("a run detail without the backend result is not ready", async () => {
    stubRun({ manufacturer: "Ashcroft", model: "1032", detected_type: "pressure gauge" });
    renderScreen();
    await screen.findByText("Ashcroft 1032");   // the run detail has loaded
    expect(screen.getByText("Need a little more")).toBeTruthy();
    expect(screen.queryByText("Part identified")).toBeNull();
    expect((await findOptionsButton()).disabled).toBe(true);
  });
});

describe("a ready run", () => {
  it("shows Part identified and enables Find options only when the backend says ready", async () => {
    stubRun(
      { manufacturer: "Ashcroft", model: "1032", detected_type: "pressure gauge" },
      { ready: true, missing_attrs: [], missing_labels: [] },
    );
    renderScreen();
    await screen.findByText("Part identified");
    const button = await findOptionsButton();
    await waitFor(() => expect(button.disabled).toBe(false));
    expect(screen.queryByTestId("readiness-still-needed")).toBeNull();
  });

  it("follows the backend even where the specs alone look incomplete", async () => {
    // No manufacturer in the specs: the card must not second-guess a backend "ready".
    stubRun(
      { part_number: "1032-XYZ", detected_type: "pressure gauge" },
      { ready: true, missing_attrs: [], missing_labels: [] },
    );
    renderScreen();
    await screen.findByText("Part identified");
    const button = await findOptionsButton();
    await waitFor(() => expect(button.disabled).toBe(false));
  });
});
