// @vitest-environment jsdom
/**
 * T5 — OpenRequests (the portal's path-B quote section, Night 11).
 *
 * Characterises: the flag-off posture (both endpoints 404 → renders NOTHING,
 * no empty shells — the claim portal looks exactly as it did before Night 11),
 * the data render (rows, quoted badges, action labels), the quote-history
 * section with its status-chip verbatim fallback, and the empty-but-on
 * posture (200 with empty lists → still nothing fabricated).
 *
 * All HTTP mocked at the global-fetch seam (gate finding I3).
 */
import { describe, it, expect } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import { OpenRequests } from "../open-requests";
import { stubFetch, jsonResponse, notFound } from "@/test-support/fetch-seam";
import { openRequest, quoteHistoryRow } from "@/test-support/fixtures";

const TOKEN = "claim_tok_9f3k2m";
const OPEN_URL = `/api/portal/${TOKEN}/open-requests`;
const QUOTES_URL = `/api/portal/${TOKEN}/quotes`;

/** Route the two GETs (and the optional submit POST) per the test's needs. */
function setup(opts: {
  open?: () => Response;
  quotes?: () => Response;
  submit?: () => Response;
} = {}) {
  const calls = stubFetch((call) => {
    if (call.url === OPEN_URL && (call.init?.method ?? "GET") === "GET") {
      return opts.open ? opts.open() : notFound();
    }
    if (call.url === QUOTES_URL && call.init?.method === "POST") {
      return opts.submit ? opts.submit() : notFound();
    }
    if (call.url === QUOTES_URL && (call.init?.method ?? "GET") === "GET") {
      return opts.quotes ? opts.quotes() : notFound();
    }
    return notFound();
  });
  const utils = render(<OpenRequests token={TOKEN} />);
  return { calls, ...utils };
}

/** Flush the mount effect's fetch → setState cycle. */
async function settle() {
  await act(async () => {
    await Promise.resolve();
  });
}

describe("OpenRequests — flag-off posture (QUOTE_SUBMIT_V1 off)", () => {
  it("renders NOTHING when both endpoints 404 — no empty shells", async () => {
    const { calls, container } = setup(); // everything 404
    // Not vacuous: both fetches really happened and really failed…
    await waitFor(() =>
      expect(calls.map((c) => c.url)).toEqual(expect.arrayContaining([OPEN_URL, QUOTES_URL])),
    );
    await settle();
    // …and the component still renders no text, no sections, no placeholders.
    expect(container.textContent ?? "").toBe("");
    expect(container.querySelectorAll("section").length).toBe(0);
    expect(screen.queryByRole("heading", { name: "Open requests for you" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Your quotes" })).toBeNull();
  });
});

describe("OpenRequests — data render (feature on)", () => {
  it("renders the supplier's own open RFQ rows with qty and action — exactly one row, nothing fabricated", async () => {
    setup({
      open: () => jsonResponse(200, { requests: [openRequest()] }),
      quotes: () => jsonResponse(200, { quotes: [] }),
    });
    await screen.findByRole("region", { name: "Open requests" });
    expect(screen.getByRole("heading", { name: "Open requests for you" })).toBeTruthy();
    expect(screen.getByText("Gusher Pumps — 3x4x14H")).toBeTruthy();
    expect(screen.getByText(/Qty 2/)).toBeTruthy();
    expect(screen.getByText(/requested 2026-09-18/)).toBeTruthy();
    // Unquoted request → the plain call to action, no badge.
    expect(screen.getByRole("button", { name: "Quote this" })).toBeTruthy();
    // No fabricated/placeholder rows: exactly the one real request (gate I4).
    expect(document.querySelectorAll(".portal-request-row").length).toBe(1);
    expect(document.body.textContent ?? "").not.toMatch(/example\.com|sample|placeholder/i);
  });

  it("distinguishes active vs in-review quoted badges and switches the action to revise", async () => {
    setup({
      open: () =>
        jsonResponse(200, {
          requests: [
            openRequest({
              run_id: "run_001",
              quoted: { status: "active", unit_price: 1250, submitted_at: "2026-09-19T09:00:00Z" },
            }),
            openRequest({
              run_id: "run_002",
              quoted: { status: "review", unit_price: 980, submitted_at: "2026-09-19T10:00:00Z" },
            }),
          ],
        }),
      quotes: () => jsonResponse(200, { quotes: [] }),
    });
    await screen.findByText("✓ Quoted $1250");
    expect(screen.getByText("⏳ Quote in review ($980)")).toBeTruthy();
    // Both quoted rows offer revision, not first-quote.
    const buttons = screen.getAllByRole("button", { name: "Revise quote" });
    expect(buttons.length).toBe(2);
    expect(screen.queryByRole("button", { name: "Quote this" })).toBeNull();
  });
});

describe("OpenRequests — quote history (feature on)", () => {
  it("renders the supplier's own quote history with status chip and date", async () => {
    setup({
      open: () => jsonResponse(200, { requests: [] }),
      quotes: () => jsonResponse(200, { quotes: [quoteHistoryRow()] }),
    });
    await screen.findByRole("region", { name: "Your quotes" });
    expect(screen.getByRole("heading", { name: "Your quotes" })).toBeTruthy();
    expect(screen.getByText("3x4x14H")).toBeTruthy();
    expect(screen.getByText("$1250")).toBeTruthy();
    expect(screen.getByText("Active")).toBeTruthy();
    expect(screen.getByText("2026-09-19")).toBeTruthy();
  });

  it("falls through verbatim for an unknown status — never masked", async () => {
    setup({
      open: () => jsonResponse(200, { requests: [] }),
      quotes: () =>
        jsonResponse(200, { quotes: [quoteHistoryRow({ status: "escalated_by_rep" })] }),
    });
    await screen.findByRole("region", { name: "Your quotes" });
    expect(screen.getByText("escalated_by_rep")).toBeTruthy();
    expect(screen.queryByText("Active")).toBeNull();
  });
});

describe("OpenRequests — feature on but no data", () => {
  it("hides both sections on 200-empty lists — no 'no requests yet' filler", async () => {
    const { calls, container } = setup({
      open: () => jsonResponse(200, { requests: [] }),
      quotes: () => jsonResponse(200, { quotes: [] }),
    });
    await waitFor(() => expect(calls.length).toBe(2));
    await settle();
    expect(container.textContent ?? "").toBe("");
    expect(container.querySelectorAll("section").length).toBe(0);
  });
});
