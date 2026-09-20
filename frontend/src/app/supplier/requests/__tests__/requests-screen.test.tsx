// @vitest-environment jsdom
/**
 * Arc 3 T6 — /supplier/requests, the session RFQ inbox.
 *
 * The property worth proving is that this is the SAME component the claim
 * page renders, fetching through a different door — so the tests assert on
 * the network (only /api/supplier/*, never a token path) and on the empty
 * state (nothing fabricated), not on a second implementation.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SupplierRequestsPage from "../page";
import {
  headerValue,
  jsonResponse,
  notFound,
  stubFetch,
  unauthorized,
} from "@/test-support/fetch-seam";
import {
  openRequest,
  quoteHistoryRow,
  supplierMe,
} from "@/test-support/fixtures";

const FLAG = "NEXT_PUBLIC_SUPPLIER_SESSION_V1";
const replace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/supplier/requests",
}));

beforeEach(() => {
  replace.mockClear();
  vi.stubEnv(FLAG, "1");
});

afterEach(() => {
  vi.unstubAllEnvs();
});

/** A backend with a session, `requests` rows and `quotes` rows. */
function stubInbox(requests: unknown[], quotes: unknown[]) {
  return stubFetch((call) => {
    if (call.url === "/api/supplier/me") return jsonResponse(200, supplierMe());
    if (call.url === "/api/supplier/requests") {
      return jsonResponse(200, { requests });
    }
    if (call.url === "/api/supplier/quotes" &&
        (call.init?.method ?? "GET") === "GET") {
      return jsonResponse(200, { quotes });
    }
    return jsonResponse(200, { ok: true });
  });
}

// ---------------------------------------------------------------------------
// Rows
// ---------------------------------------------------------------------------

describe("session inbox — rendering the account's rows", () => {
  it("renders the open requests returned for the session", async () => {
    stubInbox(
      [openRequest({ run_id: "run_a", manufacturer: "Gusher Pumps",
                     part_number: "3x4x14H", quantity: 2 })],
      [],
    );
    render(<SupplierRequestsPage />);
    expect(await screen.findByText("Gusher Pumps — 3x4x14H")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Quote this" })).toBeTruthy();
  });

  it("renders the account's quote history", async () => {
    stubInbox([], [quoteHistoryRow({ quoted_part_number: "3x4x14H",
                                     unit_price: 1250, status: "active" })]);
    render(<SupplierRequestsPage />);
    expect(await screen.findByText("Your quotes")).toBeTruthy();
    expect(screen.getByText("Active")).toBeTruthy();
  });

  it("shows the signed-in account and member in the chrome", async () => {
    stubInbox([openRequest()], []);
    render(<SupplierRequestsPage />);
    expect(await screen.findByText("sealit.example.com")).toBeTruthy();
    expect(screen.getByText("sales@sealit.example.com")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// The empty state — the thing most likely to get "helpfully" fabricated
// ---------------------------------------------------------------------------

describe("session inbox — the empty state fabricates nothing", () => {
  it("says there is nothing, and invents no rows", async () => {
    stubInbox([], []);
    render(<SupplierRequestsPage />);
    expect(await screen.findByText("No open requests right now")).toBeTruthy();
    // No fabricated request row, no fabricated history row, no count.
    expect(screen.queryByRole("button", { name: "Quote this" })).toBeNull();
    expect(screen.queryByText("Open requests for you")).toBeNull();
    expect(screen.queryByText("Your quotes")).toBeNull();
    expect(document.body.textContent).not.toMatch(/\b0 (open )?requests?\b/i);
  });

  it("does not flash the empty state before the data arrives", async () => {
    // A supplier WITH requests must never see "no open requests" first.
    stubInbox([openRequest()], []);
    render(<SupplierRequestsPage />);
    await screen.findByText("Open requests for you");
    expect(screen.queryByText("No open requests right now")).toBeNull();
  });

  it("shows the empty state when the quote feature is off (uniform 404s)", async () => {
    // QUOTE_SUBMIT_V1 off ⇒ both endpoints 404 ⇒ the component renders
    // nothing ⇒ the page still says something honest rather than going blank.
    stubFetch((call) =>
      call.url === "/api/supplier/me"
        ? jsonResponse(200, supplierMe())
        : notFound(),
    );
    render(<SupplierRequestsPage />);
    expect(await screen.findByText("No open requests right now")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Network reach
// ---------------------------------------------------------------------------

describe("session inbox — network reach", () => {
  it("calls ONLY /api/supplier/* — never a token path, never admin", async () => {
    const calls = stubInbox([openRequest()], [quoteHistoryRow()]);
    render(<SupplierRequestsPage />);
    await screen.findByText("Open requests for you");
    expect(calls.length).toBeGreaterThan(0);
    expect(calls.every((c) => /^\/api\/supplier\//.test(c.url))).toBe(true);
    expect(calls.some((c) => c.url.includes("/api/portal/"))).toBe(false);
    expect(calls.some((c) => c.url.includes("/api/admin"))).toBe(false);
  });

  it("sends the cookie and never an Authorization header", async () => {
    const calls = stubInbox([openRequest()], []);
    render(<SupplierRequestsPage />);
    await screen.findByText("Open requests for you");
    for (const c of calls) {
      expect(c.init?.credentials).toBe("include");
      expect(headerValue(c, "authorization")).toBeNull();
    }
  });

  it("submits a quote through the session endpoint", async () => {
    const calls = stubInbox([openRequest({ run_id: "run_a", quantity: 2 })], []);
    const user = userEvent.setup();
    render(<SupplierRequestsPage />);
    await screen.findByText("Open requests for you");
    await user.click(screen.getByRole("button", { name: "Quote this" }));
    await user.type(
      screen.getByLabelText("Your quote / reference number"), "Q-77");
    await user.type(screen.getByLabelText("Unit price (USD)"), "1250");
    await user.click(screen.getByLabelText("In stock now"));
    await user.click(screen.getByRole("button", { name: "Submit quote" }));

    await waitFor(() =>
      expect(
        calls.some(
          (c) => c.url === "/api/supplier/quotes" && c.init?.method === "POST",
        ),
      ).toBe(true),
    );
    const post = calls.find(
      (c) => c.url === "/api/supplier/quotes" && c.init?.method === "POST",
    )!;
    expect(JSON.parse(String(post.init?.body)).run_id).toBe("run_a");
  });
});

// ---------------------------------------------------------------------------
// No session
// ---------------------------------------------------------------------------

describe("session inbox — without a session", () => {
  it("redirects to login on a 401 from /me", async () => {
    stubFetch(() => unauthorized());
    render(<SupplierRequestsPage />);
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith("/supplier/login"),
    );
  });

  it("renders no inbox content while unauthorized", async () => {
    stubFetch(() => unauthorized());
    const { container } = render(<SupplierRequestsPage />);
    await waitFor(() => expect(replace).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });
});

// ---------------------------------------------------------------------------
// Flag gate
// ---------------------------------------------------------------------------

describe("/supplier/requests route — flag gating", () => {
  it("renders NOTHING and makes no request when the flag is off", () => {
    vi.unstubAllEnvs();
    const calls = stubInbox([openRequest()], []);
    const { container } = render(<SupplierRequestsPage />);
    expect(container.textContent).toBe("");
    expect(calls).toHaveLength(0);
  });
});
