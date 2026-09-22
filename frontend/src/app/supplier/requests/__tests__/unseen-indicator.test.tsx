// @vitest-environment jsdom
/**
 * Arc 4 T11 / D5 — the "New" indicator on the inbox rows.
 *
 * THE ABSENT-FIELD CASE IS THE IMPORTANT ONE. `seen` is added to an
 * open-request row only when the BACKEND's NOTIFICATIONS_V1 is on. So the row
 * has three states, not two:
 *
 *     seen === false      →  New (nobody at this company had opened it yet)
 *     seen === true       →  no badge
 *     seen === undefined  →  no badge  ← a flags-off backend, NOT "unread"
 *
 * Treating `undefined` as falsy — the obvious `!r.seen` — would badge every row
 * of every supplier forever the moment the frontend flag went on ahead of the
 * backend's. That is the bug these tests exist to prevent.
 *
 * The indicator is asserted through the SESSION door and the TOKEN door,
 * because both render the same component and the backend writes the view from
 * both routes.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import SupplierRequestsPage from "../page";
import { OpenRequests } from "@/app/portal/[token]/open-requests";
import type { OpenRequest } from "@/lib/portal-api";
import {
  jsonResponse,
  notFound,
  stubFetch,
  type FetchCall,
} from "@/test-support/fetch-seam";
import { supplierMe } from "@/test-support/fixtures";

const SESSION_FLAG = "NEXT_PUBLIC_SUPPLIER_SESSION_V1";
const NOTIFY_FLAG = "NEXT_PUBLIC_NOTIFICATIONS_V1";
const TOKEN = "claim_tok_unseen";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/supplier/requests",
}));

beforeEach(() => {
  vi.stubEnv(SESSION_FLAG, "1");
  vi.stubEnv(NOTIFY_FLAG, "1");
});

afterEach(() => {
  vi.unstubAllEnvs();
});

/** A row built HERE rather than from the shared fixture: `seen` is the whole
 *  subject of this file, and the fixture is a pre-existing module this arc does
 *  not modify. */
function row(over: Partial<OpenRequest> = {}): OpenRequest {
  return {
    run_id: "run_unseen",
    manufacturer: "Gusher Pumps",
    part_number: "3x4x14H",
    quantity: 2,
    sent_at: "2026-09-18T10:00:00Z",
    quoted: null,
    ...over,
  };
}

function stubSessionInbox(requests: OpenRequest[]): FetchCall[] {
  return stubFetch((call) => {
    if (call.url === "/api/supplier/me") return jsonResponse(200, supplierMe());
    if (call.url === "/api/supplier/requests") {
      return jsonResponse(200, { requests });
    }
    if (call.url === "/api/supplier/quotes") return jsonResponse(200, { quotes: [] });
    return notFound();
  });
}

function stubTokenInbox(requests: OpenRequest[]): FetchCall[] {
  return stubFetch((call) => {
    if (call.url.includes("/open-requests")) {
      return jsonResponse(200, { requests });
    }
    if (call.url.includes(`/portal/${TOKEN}/quotes`)) {
      return jsonResponse(200, { quotes: [] });
    }
    return notFound();
  });
}

const badges = () => screen.queryAllByTestId("unseen-badge");

// ---------------------------------------------------------------------------
// The three states of `seen`
// ---------------------------------------------------------------------------

describe("unseen indicator — the session inbox", () => {
  it("badges a request nobody has opened yet", async () => {
    stubSessionInbox([row({ seen: false })]);
    render(<SupplierRequestsPage />);
    await screen.findByText(/Gusher Pumps/);
    expect(badges()).toHaveLength(1);
    expect(badges()[0].textContent).toBe("New");
  });

  it("does not badge a request that has been opened", async () => {
    stubSessionInbox([row({ seen: true })]);
    render(<SupplierRequestsPage />);
    await screen.findByText(/Gusher Pumps/);
    expect(badges()).toHaveLength(0);
  });

  it("does not badge a row with no seen field at all", async () => {
    // The backend flag is off: the field is absent. An absent value must read
    // as "not tracked", never as "unread".
    stubSessionInbox([row()]);
    render(<SupplierRequestsPage />);
    await screen.findByText(/Gusher Pumps/);
    expect(badges()).toHaveLength(0);
  });

  it("badges only the unseen rows in a mixed list", async () => {
    stubSessionInbox([
      row({ run_id: "run_new", part_number: "PN-NEW", seen: false }),
      row({ run_id: "run_old", part_number: "PN-OLD", seen: true }),
      row({ run_id: "run_untracked", part_number: "PN-UNTRACKED" }),
    ]);
    render(<SupplierRequestsPage />);
    await screen.findByText(/PN-NEW/);
    expect(badges()).toHaveLength(1);
    const badgedRow = badges()[0].closest("li");
    expect(badgedRow?.textContent).toContain("PN-NEW");
  });

  it("renders nothing new when NEXT_PUBLIC_NOTIFICATIONS_V1 is off", async () => {
    vi.stubEnv(NOTIFY_FLAG, "");
    stubSessionInbox([row({ seen: false })]);
    render(<SupplierRequestsPage />);
    await screen.findByText(/Gusher Pumps/);
    expect(badges()).toHaveLength(0);
    expect(screen.queryByText("New")).toBeNull();
  });

  it("leaves the rest of the row untouched", async () => {
    stubSessionInbox([row({ seen: false })]);
    render(<SupplierRequestsPage />);
    await screen.findByText(/Gusher Pumps/);
    expect(screen.getByText(/Qty 2/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Quote this" })).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// The token door renders the same component
// ---------------------------------------------------------------------------

describe("unseen indicator — the claim-token door", () => {
  it("badges an unseen request there too", async () => {
    stubTokenInbox([row({ seen: false })]);
    render(<OpenRequests token={TOKEN} />);
    await screen.findByText(/Gusher Pumps/);
    expect(badges()).toHaveLength(1);
  });

  it("adds no request of its own — the view is recorded server-side", async () => {
    // D5: the view is written by the route handler that already served this
    // list. A client "I rendered it" ping would be a claim by the browser, and
    // would also mean a second request per render.
    const calls = stubTokenInbox([row({ seen: false })]);
    render(<OpenRequests token={TOKEN} />);
    await screen.findByText(/Gusher Pumps/);
    expect(
      calls.filter((c) => (c.init?.method ?? "GET") !== "GET"),
    ).toHaveLength(0);
    expect(calls.map((c) => c.url).sort()).toEqual([
      `/api/portal/${TOKEN}/open-requests`,
      `/api/portal/${TOKEN}/quotes`,
    ]);
  });
});
