// @vitest-environment jsdom
/**
 * T6 — the five-field quote form characterised on BOTH frontend entry paths:
 *   A. the public /quote/{token} page (QuotePage)
 *   B. the claimed supplier's portal inline form (OpenRequests)
 * Path C (concierge/sales-assisted entry) has NO frontend surface — a scoped
 * limitation recorded as gate finding F2; there is nothing to test here.
 *
 * Covers: field render + prefills, minimal-and-honest validation (required
 * list only after a submit attempt; part number NEVER in it), the exact
 * submit payload read at the fetch seam (including the "in stock" variant and
 * the optional fold), revising framing, and sanity checks that FLAG
 * (pn-differs note + honest review copy) rather than BLOCK submission.
 */
import { describe, it, expect } from "vitest";
import { render, screen, within, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QuotePage } from "../quote-page";
import { OpenRequests } from "../../../portal/[token]/open-requests";
import { stubFetch, jsonResponse, notFound, type FetchCall } from "@/test-support/fetch-seam";
import { quoteContext, openRequest } from "@/test-support/fixtures";
import type { QuoteSubmissionBody } from "@/lib/quote-api";

const TOKEN = "quote_tok_5h7j1k";
const QUOTE_URL = `/api/quote/${TOKEN}`;

const ACTIVE_RESULT = {
  ok: true, quote_id: "q_001", status: "active" as const,
  review_reasons: [], pn_differs: false,
  valid_until: "2026-09-27T00:00:00Z", claim_pitch: false,
};

/** Path A: the public quote page with configurable GET context / POST result. */
function setupPathA(opts: { context?: () => Response; result?: () => Response } = {}) {
  const calls = stubFetch((call) => {
    if (call.url === QUOTE_URL && (call.init?.method ?? "GET") === "GET") {
      return opts.context ? opts.context() : jsonResponse(200, quoteContext());
    }
    if (call.url === QUOTE_URL && call.init?.method === "POST") {
      return opts.result ? opts.result() : jsonResponse(200, ACTIVE_RESULT);
    }
    return notFound();
  });
  const user = userEvent.setup();
  render(<QuotePage token={TOKEN} />);
  return { calls, user };
}

async function fillRequired(
  user: ReturnType<typeof userEvent.setup>,
  over: { quoteNumber?: string; price?: string; lead?: string } = {},
) {
  await user.type(screen.getByLabelText("Your quote / reference number"), over.quoteNumber ?? "Q-10412");
  await user.type(screen.getByLabelText("Unit price (USD)"), over.price ?? "1250");
  // An empty lead means the caller is exercising the in-stock path (no typing).
  const lead = over.lead ?? "3";
  if (lead) await user.type(screen.getByLabelText("Lead time in days"), lead);
}

/** The body of the (single) POST this test performed. */
function postedBody(calls: FetchCall[], url: string): QuoteSubmissionBody & { run_id?: string } {
  const post = calls.find((c) => c.url === url && c.init?.method === "POST");
  expect(post).toBeTruthy();
  return JSON.parse(String(post!.init!.body));
}

describe("T6.1 — five fields render with RFQ prefills (path A)", () => {
  it("renders the request hero, prefilled quantity and part number, and the optional fold", async () => {
    setupPathA();
    await screen.findByText("Quote request");
    expect(screen.getByRole("heading", { name: "Gusher Pumps — 3x4x14H" })).toBeTruthy();
    expect(screen.getByText("Needed by")).toBeTruthy();
    expect(screen.getByText("2026-09-27")).toBeTruthy();
    // Prefills: quantity + part number straight from the RFQ…
    expect((screen.getByLabelText("Quantity quoted") as HTMLInputElement).value).toBe("2");
    expect((screen.getByLabelText("Part number you are quoting") as HTMLInputElement).value).toBe("3x4x14H");
    // …the optional trio is folded away, one summary control…
    expect(screen.getByText("Optional: freight, validity, notes")).toBeTruthy();
    // …and NO validation message exists before the first submit attempt.
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("switches to update framing when an earlier quote exists (submission supersedes)", async () => {
    setupPathA({
      context: () =>
        jsonResponse(
          200,
          quoteContext({
            existing_quote: { status: "active", unit_price: 990, submitted_at: "2026-09-19T09:00:00Z" },
          }),
        ),
    });
    await screen.findByText("Quote request");
    expect(screen.getByRole("button", { name: "Update your quote" })).toBeTruthy();
    expect(
      screen.getByText("You quoted $990 earlier — submitting again replaces that quote."),
    ).toBeTruthy();
    expect(screen.getByText("This replaces your earlier quote for this request.")).toBeTruthy();
  });
});

describe("T6.1 — validation is minimal, honest, and only after a submit attempt", () => {
  it("lists exactly the missing required fields — never the part number", async () => {
    const { user } = setupPathA();
    await screen.findByText("Quote request");
    // Submit with only the prefills (quantity present): three fields missing.
    await user.click(screen.getByRole("button", { name: "Submit quote" }));
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toBe("Please fill in: quote number, unit price, lead time.");
    // The part number is a confirmation, not a gate — never in the error list.
    expect(alert.textContent).not.toContain("part number");
  });
});

describe("T6.2 — submit payload shape (read at the fetch seam, path A)", () => {
  it("posts the five required fields plus null optional trio; echoes the submission back", async () => {
    const { calls, user } = setupPathA();
    await screen.findByText("Quote request");
    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: "Submit quote" }));
    await screen.findByText("Quote received");
    expect(postedBody(calls, QUOTE_URL)).toEqual({
      quote_number: "Q-10412",
      unit_price: 1250,
      quantity: 2,
      lead_time: "3 days",
      part_number: "3x4x14H",
      freight: null,
      valid_until: null,
      notes: null,
    });
    // The confirmation echoes what the supplier entered, not a canned summary.
    expect(screen.getByText("$1250")).toBeTruthy();
    expect(screen.getByText("Your ref")).toBeTruthy();
    expect(screen.getByText("Q-10412")).toBeTruthy();
  });

  it("posts lead_time 'in stock' when the in-stock box is checked (days input disabled)", async () => {
    const { calls, user } = setupPathA();
    await screen.findByText("Quote request");
    await fillRequired(user, { lead: "" });
    await user.click(screen.getByLabelText("In stock now"));
    expect((screen.getByLabelText("Lead time in days") as HTMLInputElement).disabled).toBe(true);
    await user.click(screen.getByRole("button", { name: "Submit quote" }));
    await screen.findByText("Quote received");
    expect(postedBody(calls, QUOTE_URL).lead_time).toBe("in stock");
  });

  it("carries the optional fold through when filled (freight / valid-until / notes)", async () => {
    const { calls, user } = setupPathA();
    await screen.findByText("Quote request");
    await fillRequired(user);
    await user.click(screen.getByText("Optional: freight, validity, notes"));
    await user.type(screen.getByLabelText("Freight / shipping"), "$25");
    await user.type(screen.getByLabelText("Notes"), "Ship from Dallas");
    fireEvent.change(screen.getByLabelText("Quote valid until"), { target: { value: "2026-09-25" } });
    await user.click(screen.getByRole("button", { name: "Submit quote" }));
    await screen.findByText("Quote received");
    expect(postedBody(calls, QUOTE_URL)).toMatchObject({
      freight: "$25",
      valid_until: "2026-09-25",
      notes: "Ship from Dallas",
    });
  });
});

describe("T6.3 — entry path B: the portal inline form reaches the same five fields", () => {
  it("submits through /api/portal/{token}/quotes with the run_id, same field set", async () => {
    const PTOKEN = "claim_tok_b2c3";
    const OPEN_URL = `/api/portal/${PTOKEN}/open-requests`;
    const QUOTES_URL = `/api/portal/${PTOKEN}/quotes`;
    const calls = stubFetch((call) => {
      if (call.url === OPEN_URL && (call.init?.method ?? "GET") === "GET") {
        return jsonResponse(200, { requests: [openRequest()] });
      }
      if (call.url === QUOTES_URL && call.init?.method === "POST") {
        return jsonResponse(200, ACTIVE_RESULT);
      }
      if (call.url === QUOTES_URL && (call.init?.method ?? "GET") === "GET") {
        return jsonResponse(200, { quotes: [] });
      }
      return notFound();
    });
    const user = userEvent.setup();
    render(<OpenRequests token={PTOKEN} />);
    await screen.findByRole("button", { name: "Quote this" });
    await user.click(screen.getByRole("button", { name: "Quote this" }));
    // The SAME five-field form appears inline, prefilled from the open RFQ.
    const row = screen.getByText("Gusher Pumps — 3x4x14H").closest("li")!;
    const form = within(row).getByLabelText("Your quote / reference number");
    expect((within(row).getByLabelText("Quantity quoted") as HTMLInputElement).value).toBe("2");
    await user.type(form, "Q-22113");
    await user.type(within(row).getByLabelText("Unit price (USD)"), "1100");
    await user.type(within(row).getByLabelText("Lead time in days"), "5");
    await user.click(within(row).getByRole("button", { name: "Submit quote" }));
    // The submit POST fires from an async handler — wait for it to land.
    await waitFor(() =>
      expect(calls.some((c) => c.url === QUOTES_URL && c.init?.method === "POST")).toBe(true),
    );
    const body = postedBody(calls, QUOTES_URL);
    expect(body.run_id).toBe("run_001"); // path B binds the quote to the open request
    expect(body).toMatchObject({
      quote_number: "Q-22113",
      unit_price: 1100,
      quantity: 2,
      lead_time: "5 days",
      part_number: "3x4x14H",
    });
  });
});

describe("T6.4 — sanity checks FLAG, never block", () => {
  it("an edited part number gets the alternative note and STILL submits (no client gate)", async () => {
    const { calls, user } = setupPathA();
    await screen.findByText("Quote request");
    await fillRequired(user);
    const pn = screen.getByLabelText("Part number you are quoting");
    await user.clear(pn);
    await user.type(pn, "3x4x14H-ALT");
    // The note is honest framing — role=status, not an error…
    const note = screen.getByRole("status");
    expect(note.textContent).toContain("This differs from the requested part number (3x4x14H)");
    expect(screen.queryByRole("alert")).toBeNull();
    // …and submission proceeds anyway (the wrong-part gate is server-side).
    await user.click(screen.getByRole("button", { name: "Submit quote" }));
    await screen.findByText("Quote received");
    expect(postedBody(calls, QUOTE_URL).part_number).toBe("3x4x14H-ALT");
  });

  it("a review-flagged pn-differs result confirms honestly — never a fake 'live to the buyer'", async () => {
    setupPathA({
      result: () =>
        jsonResponse(200, {
          ...ACTIVE_RESULT,
          status: "review",
          pn_differs: true,
          review_reasons: ["pn_differs"],
        }),
    });
    const user = userEvent.setup();
    await screen.findByText("Quote request");
    await fillRequired(user);
    const pn = screen.getByLabelText("Part number you are quoting");
    await user.clear(pn);
    await user.type(pn, "3x4x14H-ALT");
    await user.click(screen.getByRole("button", { name: "Submit quote" }));
    await screen.findByText("Quote received");
    expect(screen.getByText(/being double-checked on our side/)).toBeTruthy();
    expect(screen.getByText(/differs from the one requested/)).toBeTruthy();
    expect(screen.queryByText(/in front of the buyer now/)).toBeNull();
  });
});
