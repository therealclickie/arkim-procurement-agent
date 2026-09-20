// @vitest-environment jsdom
/**
 * Arc 3 T8 — quote submission under a session.
 *
 * The form is the SAME five-required-plus-optional-trio QuoteForm the public
 * /quote/{token} page uses; only the door changes. So what is asserted here
 * is the payload and the honesty of the confirmation — parity with the
 * pre-existing quote tests, through /api/supplier/quotes instead of a token
 * path.
 *
 * "Flag-not-block" is the settled behaviour and is unchanged: a quote the
 * backend marks for review is still ACCEPTED, and the supplier is told so
 * honestly rather than being silently told it is live.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SupplierRequestsPage from "../page";
import {
  jsonResponse,
  stubFetch,
  type FetchCall,
} from "@/test-support/fetch-seam";
import { openRequest, supplierMe } from "@/test-support/fixtures";

const FLAG = "NEXT_PUBLIC_SUPPLIER_SESSION_V1";
const SUBMIT_URL = "/api/supplier/quotes";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/supplier/requests",
}));

beforeEach(() => {
  vi.stubEnv(FLAG, "1");
});

afterEach(() => {
  vi.unstubAllEnvs();
});

/**
 * A backend that accepts one quote. `submitResponse` decides what the
 * accepted quote comes back as, and `afterSubmit` is the open-requests row
 * the refresh then returns (the row is how the supplier learns the outcome).
 */
function stubQuoting(
  submitResponse: Record<string, unknown>,
  afterSubmit: ReturnType<typeof openRequest> | null = null,
): FetchCall[] {
  let submitted = false;
  return stubFetch((call) => {
    if (call.url === "/api/supplier/me") return jsonResponse(200, supplierMe());
    if (call.url === "/api/supplier/requests") {
      return jsonResponse(200, {
        requests: [
          submitted && afterSubmit
            ? afterSubmit
            : openRequest({ run_id: "run_a", part_number: "3x4x14H",
                            quantity: 2 }),
        ],
      });
    }
    if (call.url === SUBMIT_URL && (call.init?.method ?? "GET") === "GET") {
      return jsonResponse(200, { quotes: [] });
    }
    if (call.url === SUBMIT_URL && call.init?.method === "POST") {
      submitted = true;
      return jsonResponse(200, submitResponse);
    }
    return jsonResponse(200, { ok: true });
  });
}

const ACCEPTED = {
  ok: true,
  quote_id: "q_1",
  status: "active",
  review_reasons: [],
  pn_differs: false,
  valid_until: "2026-10-01T00:00:00Z",
};

async function openTheForm() {
  const user = userEvent.setup();
  render(<SupplierRequestsPage />);
  await screen.findByText("Open requests for you");
  await user.click(screen.getByRole("button", { name: "Quote this" }));
  await screen.findByLabelText("Your quote / reference number");
  return user;
}

function submittedBody(calls: FetchCall[]) {
  const post = calls.find(
    (c) => c.url === SUBMIT_URL && c.init?.method === "POST",
  );
  expect(post).toBeTruthy();
  return JSON.parse(String(post!.init!.body));
}

// ---------------------------------------------------------------------------
// The five required fields
// ---------------------------------------------------------------------------

describe("session quote — required fields", () => {
  it("submits run, number, price, quantity and lead time", async () => {
    const calls = stubQuoting(ACCEPTED);
    const user = await openTheForm();
    await user.type(
      screen.getByLabelText("Your quote / reference number"), "Q-901");
    await user.type(screen.getByLabelText("Unit price (USD)"), "1250");
    await user.type(screen.getByLabelText("Lead time in days"), "5");
    await user.click(screen.getByRole("button", { name: "Submit quote" }));

    await waitFor(() =>
      expect(calls.some((c) => c.url === SUBMIT_URL &&
                               c.init?.method === "POST")).toBe(true));
    expect(submittedBody(calls)).toMatchObject({
      run_id: "run_a",
      quote_number: "Q-901",
      unit_price: 1250,
      quantity: 2,          // prefilled from the request
      lead_time: "5 days",
    });
  });

  it("sends `in stock` as the lead time when the toggle is used", async () => {
    const calls = stubQuoting(ACCEPTED);
    const user = await openTheForm();
    await user.type(
      screen.getByLabelText("Your quote / reference number"), "Q-902");
    await user.type(screen.getByLabelText("Unit price (USD)"), "999");
    await user.click(screen.getByLabelText("In stock now"));
    await user.click(screen.getByRole("button", { name: "Submit quote" }));

    await waitFor(() => expect(
      calls.some((c) => c.url === SUBMIT_URL && c.init?.method === "POST")
    ).toBe(true));
    expect(submittedBody(calls).lead_time).toBe("in stock");
  });
});

// ---------------------------------------------------------------------------
// The optional trio
// ---------------------------------------------------------------------------

describe("session quote — optional fields", () => {
  it("carries freight, valid-until and notes when supplied", async () => {
    const calls = stubQuoting(ACCEPTED);
    const user = await openTheForm();
    await user.type(
      screen.getByLabelText("Your quote / reference number"), "Q-903");
    await user.type(screen.getByLabelText("Unit price (USD)"), "1000");
    await user.click(screen.getByLabelText("In stock now"));
    await user.type(screen.getByLabelText("Freight / shipping"), "$25");
    await user.type(screen.getByLabelText("Notes"), "Ship from Dallas");
    fireEvent.change(screen.getByLabelText("Quote valid until"), {
      target: { value: "2026-09-25" },
    });
    await user.click(screen.getByRole("button", { name: "Submit quote" }));

    await waitFor(() => expect(
      calls.some((c) => c.url === SUBMIT_URL && c.init?.method === "POST")
    ).toBe(true));
    expect(submittedBody(calls)).toMatchObject({
      freight: "$25",
      notes: "Ship from Dallas",
      valid_until: "2026-09-25",
    });
  });

  it("sends null, not an empty string, for omitted optionals", async () => {
    const calls = stubQuoting(ACCEPTED);
    const user = await openTheForm();
    await user.type(
      screen.getByLabelText("Your quote / reference number"), "Q-904");
    await user.type(screen.getByLabelText("Unit price (USD)"), "1000");
    await user.click(screen.getByLabelText("In stock now"));
    await user.click(screen.getByRole("button", { name: "Submit quote" }));

    await waitFor(() => expect(
      calls.some((c) => c.url === SUBMIT_URL && c.init?.method === "POST")
    ).toBe(true));
    const body = submittedBody(calls);
    expect(body.freight).toBeNull();
    expect(body.notes).toBeNull();
    expect(body.valid_until).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Flag-not-block: a review-flagged quote is accepted, and said so honestly
// ---------------------------------------------------------------------------

describe("session quote — a review-flagged result confirms honestly", () => {
  it("shows the quote as IN REVIEW, not as live", async () => {
    const calls = stubQuoting(
      { ...ACCEPTED, status: "review", review_reasons: ["price_outlier"] },
      openRequest({
        run_id: "run_a",
        part_number: "3x4x14H",
        quantity: 2,
        quoted: { status: "review", unit_price: 1250,
                  submitted_at: "2026-09-20T10:00:00Z" },
      }),
    );
    const user = await openTheForm();
    await user.type(
      screen.getByLabelText("Your quote / reference number"), "Q-905");
    await user.type(screen.getByLabelText("Unit price (USD)"), "1250");
    await user.click(screen.getByLabelText("In stock now"));
    await user.click(screen.getByRole("button", { name: "Submit quote" }));

    // Accepted — not blocked, not an error.
    expect(await screen.findByText(/Quote in review/)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    // ...and it is not described as active.
    expect(document.body.textContent).not.toMatch(/✓ Quoted/);
    // The supplier can revise it.
    expect(screen.getByRole("button", { name: "Revise quote" })).toBeTruthy();
    expect(calls.some((c) => c.url === SUBMIT_URL &&
                             c.init?.method === "POST")).toBe(true);
  });

  it("shows an accepted active quote as quoted", async () => {
    stubQuoting(ACCEPTED, openRequest({
      run_id: "run_a",
      part_number: "3x4x14H",
      quantity: 2,
      quoted: { status: "active", unit_price: 1250,
                submitted_at: "2026-09-20T10:00:00Z" },
    }));
    const user = await openTheForm();
    await user.type(
      screen.getByLabelText("Your quote / reference number"), "Q-906");
    await user.type(screen.getByLabelText("Unit price (USD)"), "1250");
    await user.click(screen.getByLabelText("In stock now"));
    await user.click(screen.getByRole("button", { name: "Submit quote" }));
    expect(await screen.findByText(/✓ Quoted/)).toBeTruthy();
  });

  it("keeps the supplier's entries and says so when the submit fails", async () => {
    stubFetch((call) => {
      if (call.url === "/api/supplier/me") return jsonResponse(200, supplierMe());
      if (call.url === "/api/supplier/requests") {
        return jsonResponse(200, {
          requests: [openRequest({ run_id: "run_a", quantity: 2 })],
        });
      }
      if (call.url === SUBMIT_URL && (call.init?.method ?? "GET") === "GET") {
        return jsonResponse(200, { quotes: [] });
      }
      return jsonResponse(500, { detail: "boom" });
    });
    const user = await openTheForm();
    await user.type(
      screen.getByLabelText("Your quote / reference number"), "Q-907");
    await user.type(screen.getByLabelText("Unit price (USD)"), "1250");
    await user.click(screen.getByLabelText("In stock now"));
    await user.click(screen.getByRole("button", { name: "Submit quote" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/couldn't submit your quote/i);
    expect(
      (screen.getByLabelText("Your quote / reference number") as HTMLInputElement)
        .value,
    ).toBe("Q-907");
  });
});
