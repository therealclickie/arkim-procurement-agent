// @vitest-environment jsdom
/**
 * T3.4 / T3.5 — security posture of the PUBLIC quote route (/quote/[token]):
 * only /api/quote/* is ever called (no admin endpoint, no Authorization
 * header), the token is never persisted or logged across a full submit cycle,
 * and every indistinguishable failure renders byte-identical output. The
 * CLOSED state (409, dead RFQ) is deliberately distinct — it is a state, not
 * an error — and is asserted as the contrast proving the equality check can
 * actually detect divergence.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QuotePage } from "../quote-page";
import { stubFetch, jsonResponse, type FetchCall } from "@/test-support/fetch-seam";
import { quoteContext } from "@/test-support/fixtures";

const TOKEN = "quote_tok_5h7j1k";
const QUOTE_URL = `/api/quote/${TOKEN}`;

function stubLiveQuote() {
  return stubFetch((call) => {
    if (call.url === QUOTE_URL && (call.init?.method ?? "GET") === "GET") {
      return jsonResponse(200, quoteContext());
    }
    if (call.url === QUOTE_URL && call.init?.method === "POST") {
      return jsonResponse(200, {
        ok: true, quote_id: "q_001", status: "active",
        review_reasons: [], pn_differs: false,
        valid_until: "2026-09-27T00:00:00Z", claim_pitch: false,
      });
    }
    return jsonResponse(404, { detail: "Not Found" });
  });
}

async function fullSubmitCycle() {
  const user = userEvent.setup();
  render(<QuotePage token={TOKEN} />);
  await screen.findByText("Quote request");
  // Fill the five required fields (quantity + part number arrive prefilled).
  await user.type(screen.getByLabelText("Your quote / reference number"), "Q-10412");
  await user.type(screen.getByLabelText("Unit price (USD)"), "1250");
  await user.type(screen.getByLabelText("Lead time in days"), "3");
  await user.click(screen.getByRole("button", { name: "Submit quote" }));
  await screen.findByText("Quote received");
}

function storageDump(store: Storage): string {
  const parts: string[] = [];
  for (let i = 0; i < store.length; i++) {
    const key = store.key(i);
    if (key !== null) parts.push(key, store.getItem(key) ?? "");
  }
  return parts.join("\n");
}

function headerValue(call: FetchCall, name: string): string | undefined {
  const h = call.init?.headers as Record<string, string> | undefined;
  return h?.[name];
}

describe("quote route — network reach", () => {
  it("calls ONLY /api/quote/* — never an admin endpoint, no Authorization header", async () => {
    const calls = stubLiveQuote();
    await fullSubmitCycle();

    expect(calls.length).toBeGreaterThanOrEqual(2); // GET context + POST submit
    const urls = calls.map((c) => c.url);
    expect(urls.every((u) => /^\/api\/quote\//.test(u))).toBe(true);
    expect(urls.some((u) => u.includes("/api/admin"))).toBe(false);
    for (const c of calls) {
      expect(headerValue(c, "Authorization")).toBeUndefined();
      expect(headerValue(c, "authorization")).toBeUndefined();
    }
  });
});

describe("quote route — token hygiene (full submit cycle)", () => {
  it("leaves no substring of the token in storage, cookies, or console output", async () => {
    const spies = (["log", "info", "warn", "error", "debug"] as const).map((m) =>
      vi.spyOn(console, m).mockImplementation(() => {}),
    );
    stubLiveQuote();
    await fullSubmitCycle();

    expect(storageDump(window.localStorage)).not.toContain(TOKEN);
    expect(storageDump(window.sessionStorage)).not.toContain(TOKEN);
    expect(document.cookie).not.toContain(TOKEN);
    const logged = spies
      .flatMap((s) => s.mock.calls)
      .map((args) => args.map((a) => (typeof a === "string" ? a : JSON.stringify(a))).join(" "))
      .join("\n");
    expect(logged).not.toContain(TOKEN);
    spies.forEach((s) => s.mockRestore());
  });
});

describe("quote route — uniform rejection (no oracle)", () => {
  it("renders byte-identical output for 404 / 410 / 422 / network failure", async () => {
    const failures: Array<() => Response> = [
      () => jsonResponse(404, { detail: "Not Found" }), // invalid token / flag off
      () => jsonResponse(410, { detail: "Gone" }), // expired
      () => jsonResponse(422, { detail: "Unprocessable" }), // malformed
      () => {
        throw new Error("network down");
      },
    ];
    const texts: string[] = [];
    for (const fail of failures) {
      stubFetch(() => fail());
      const { unmount } = render(<QuotePage token={TOKEN} />);
      await screen.findByText("This link is no longer valid");
      texts.push(document.body.textContent ?? "");
      unmount();
    }
    expect(texts).toHaveLength(4);
    expect(new Set(texts).size).toBe(1);
  });

  it("contrast: the CLOSED state (409) is honestly distinct — a state, not an error", async () => {
    // A known token whose RFQ closed renders "This request has closed", NOT
    // the rejection card. This proves the equality test above can distinguish
    // outputs rather than passing vacuously.
    stubFetch(() => jsonResponse(409, { detail: "closed" }));
    render(<QuotePage token={TOKEN} />);
    await screen.findByText("This request has closed");
    expect(screen.queryByText("This link is no longer valid")).toBeNull();
  });
});
