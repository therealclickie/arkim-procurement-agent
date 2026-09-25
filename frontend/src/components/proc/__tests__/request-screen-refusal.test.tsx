// @vitest-environment jsdom
/**
 * PH-01 round 3 — every confirm refusal renders on its card.
 *
 * confirm-intake's readiness refusals (api_server.confirm_intake ->
 * utils/intake_readiness) used to fall through to the generic "Couldn't start
 * sourcing — please try again." toast, and the `override: "source_anyway"` the
 * backend advertised was unreachable. These tests drive the real RequestScreen
 * through the fetch seam (a ?resume= deep link, so no intake chat is needed) and
 * assert on the rendered refusal and on the network call Source anyway makes.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { jsonResponse, stubFetch, type FetchCall } from "@/test-support/fetch-seam";
import { RequestScreen } from "../request-screen";

const RUN = "run-ph01";
const push = vi.fn();
const fire = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(`resume=${RUN}`),
  usePathname: () => "/request",
}));

vi.mock("../proc-shell", () => ({ useProcToast: () => fire }));

const GENERIC_TOAST = "Couldn't start sourcing — please try again.";

/** Identity-sufficient on the FRONTEND's own check, so "Find options" is enabled. */
const specs = { manufacturer: "Ashcroft", model: "1032", detected_type: "pressure gauge" };

const IDENTITY_REFUSAL = {
  message:
    "This request has no manufacturer and model, and no manufacturer part number — " +
    "there is nothing to match a supplier's listing against. Add them in the chat, " +
    "or source anyway and review the results yourself.",
  reason: "identity_insufficient",
  missing_attrs: ["manufacturer"],
  missing_labels: ["manufacturer"],
  override: "source_anyway",
  all_missing_attrs: ["manufacturer", "hygienic_certification"],
  all_missing_labels: ["manufacturer", "hygienic certification (3-A / EHEDG / none)"],
};

const HYGIENIC_REFUSAL = {
  message:
    "This is hygienic service, so the part has to match the skid: what hygienic " +
    "certification (3-A / EHEDG / none)? For the certification: 3-A, EHEDG, or none required?",
  reason: "hygienic_spec_incomplete",
  hygienic_context: ["cip"],
  missing_attrs: ["hygienic_certification"],
  missing_labels: ["hygienic certification (3-A / EHEDG / none)"],
  override: "source_anyway",
  all_missing_attrs: ["hygienic_certification"],
  all_missing_labels: ["hygienic certification (3-A / EHEDG / none)"],
};

function isConfirm(call: FetchCall): boolean {
  return (call.init?.method ?? "GET") === "POST" && call.url.includes("/confirm-intake");
}

/** A backend whose confirm refuses with `refusal` unless source_anyway=true is sent. */
function stubBackend(refusal: unknown): FetchCall[] {
  return stubFetch((call) => {
    if (isConfirm(call)) {
      if (call.url.includes("source_anyway=true")) {
        return jsonResponse(200, { run_id: RUN, phase: "sourcing" });
      }
      return jsonResponse(422, { detail: refusal });
    }
    if (call.url.endsWith(`/api/runs/${RUN}`)) {
      return jsonResponse(200, { id: RUN, phase: "intake", asset_specs: specs });
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

async function findOptions() {
  const button = await screen.findByRole("button", { name: /find options/i });
  await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  await userEvent.click(button);
}

beforeEach(() => {
  push.mockClear();
  fire.mockClear();
});

describe("the identity refusal", () => {
  it("shows the backend message and every missing item, never the generic toast", async () => {
    stubBackend(IDENTITY_REFUSAL);
    renderScreen();
    await findOptions();

    const refusal = await screen.findByTestId("readiness-refusal");
    expect(refusal.textContent).toContain(IDENTITY_REFUSAL.message);
    expect(refusal.textContent).toContain("manufacturer");
    expect(refusal.textContent).toContain("hygienic certification (3-A / EHEDG / none)");
    expect(fire).not.toHaveBeenCalledWith(GENERIC_TOAST);
    expect(push).not.toHaveBeenCalled();
  });
});

describe("the hygienic refusal", () => {
  it("shows the hygienic question and the missing certification, never the generic toast", async () => {
    stubBackend(HYGIENIC_REFUSAL);
    renderScreen();
    await findOptions();

    const refusal = await screen.findByTestId("readiness-refusal");
    expect(refusal.textContent).toContain(HYGIENIC_REFUSAL.message);
    expect(refusal.textContent).toContain("Still needed:");
    expect(refusal.textContent).toContain("hygienic certification (3-A / EHEDG / none)");
    expect(fire).not.toHaveBeenCalled();
  });
});

describe("Source anyway", () => {
  it("is explicit: disabled until the buyer acknowledges, then sends source_anyway", async () => {
    const calls = stubBackend(HYGIENIC_REFUSAL);
    renderScreen();
    await findOptions();

    const action = await screen.findByRole("button", { name: "Source anyway" });
    expect((action as HTMLButtonElement).disabled).toBe(true);
    expect(calls.filter((c) => c.url.includes("source_anyway=true"))).toHaveLength(0);

    await userEvent.click(
      screen.getByRole("checkbox", { name: /will NOT be checked against my requirement/i }),
    );
    expect((action as HTMLButtonElement).disabled).toBe(false);
    await userEvent.click(action);

    await waitFor(() => expect(push).toHaveBeenCalledWith(`/parts/${RUN}`));
    const overrides = calls.filter((c) => isConfirm(c) && c.url.includes("source_anyway=true"));
    expect(overrides).toHaveLength(1);
    expect(overrides[0].url).toContain(`/api/runs/${RUN}/confirm-intake?source_anyway=true`);
    expect(fire).not.toHaveBeenCalled();
  });

  it("is not offered when the refusal does not advertise the override", async () => {
    stubBackend({ ...IDENTITY_REFUSAL, override: undefined });
    renderScreen();
    await findOptions();

    await screen.findByTestId("readiness-refusal");
    expect(screen.queryByRole("button", { name: "Source anyway" })).toBeNull();
    expect(fire).not.toHaveBeenCalled();
  });
});

describe("a 422 without a reason", () => {
  it("still falls back to the generic toast", async () => {
    stubFetch((call) => {
      if (isConfirm(call)) {
        return jsonResponse(422, { detail: "No asset specs captured yet — complete intake chat first" });
      }
      return jsonResponse(200, { id: RUN, phase: "intake", asset_specs: specs });
    });
    renderScreen();
    await findOptions();

    await waitFor(() => expect(fire).toHaveBeenCalledWith(GENERIC_TOAST));
    expect(screen.queryByTestId("readiness-refusal")).toBeNull();
  });
});
