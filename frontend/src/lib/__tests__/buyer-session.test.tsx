// @vitest-environment jsdom
/**
 * Arc 6 T8 — the buyer session in the UI.
 *
 *  1. Controls are hidden by permission: a Requester sees no select/order and
 *     no approve controls; a Buyer/Approver does. With the flag off (no
 *     session provider) every control renders exactly as today.
 *  2. A hidden control's endpoint is still refused by the SERVER: the client
 *     sends the call with the cookie and surfaces the 403 — it never decides
 *     authorisation itself (the backend proof is test_buyer_hidden_controls.py).
 *  3. No session value in JS-readable storage after a full
 *     login → verify → act → logout cycle.
 *  4. The header shows the authenticated company; the guard sends a signed-out
 *     visitor to /login; verify needs an explicit click.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  consoleDump,
  jsonResponse,
  spyConsole,
  storageDump,
  stubFetch,
  unauthorized,
} from "@/test-support/fetch-seam";
import { BuyerSessionProvider, sessionFromMe } from "@/lib/buyer-session";
import type { BuyerCapability, BuyerMe, BuyerRole } from "@/lib/buyer-api";
import { ApprovalActions } from "@/components/proc/approval-actions";
import { OptionsScreen } from "@/components/proc/options-screen";
import { ProcShell } from "@/components/proc/proc-shell";
import { BuyerLoginScreen } from "@/app/login/login-screen";
import { BuyerVerifyScreen, CONTINUE_LABEL } from "@/app/verify/verify-screen";
import { approveRun } from "@/lib/api";
import { ApiError } from "@/lib/query-client";
import type { SourcingRunDetail } from "@/types";

const FLAG = "NEXT_PUBLIC_BUYER_SESSION_V1";
const RUN = "run-a";
const LINK_TOKEN = "buyer_magic_raw_tok_9f3e";
const SESSION_SECRET = "buyer_session_secret_77aa";

const replace = vi.fn();
const push = vi.fn();
let search = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push, refresh: vi.fn() }),
  useSearchParams: () => search,
  usePathname: () => "/",
}));

beforeEach(() => {
  replace.mockClear();
  push.mockClear();
  search = new URLSearchParams();
});

afterEach(() => {
  vi.unstubAllEnvs();
  window.localStorage.clear();
  window.sessionStorage.clear();
});

const PERMS: Record<BuyerRole, BuyerCapability[]> = {
  REQUESTER: ["raise_request", "view_company"],
  BUYER: ["raise_request", "view_company", "select_and_order", "approve_within_limit"],
  APPROVER: ["raise_request", "view_company", "select_and_order", "approve_within_limit",
             "second_approval"],
  ADMIN: ["raise_request", "view_company", "select_and_order", "approve_within_limit",
          "second_approval", "override_limit", "set_approval_limit", "toggle_admin_override",
          "manage_members"],
};

function me(role: BuyerRole): BuyerMe {
  return {
    company: { id: "company-bayfoods", name: "Bay Foods", facility_ids: ["fac-stockton"] },
    member: { id: `m-${role}`, email: `${role.toLowerCase()}@bayfoods.com`, role,
              status: "ACTIVE", created_at: "2026-09-26T00:00:00Z", permissions: PERMS[role] },
  };
}

function withClient(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

function asRole(role: BuyerRole | null, node: React.ReactNode) {
  return withClient(role ? (
    <BuyerSessionProvider session={sessionFromMe(me(role))}>{node}</BuyerSessionProvider>
  ) : node);
}

const AWAITING = {
  id: RUN,
  phase: "pending_first_approval",
  selected_candidate: { candidate_id: "Vend-t1-0", _approval_path: { approvers_required: 1 } },
  sourcing_results: { tier1: [], tier2: [], tier3: [] },
  approval_history: [],
} as unknown as SourcingRunDetail;

const CANDIDATE = {
  id: "Vend-t1-0", vendorName: "Vend", vendorType: "distributor", tier: 1, price: 10,
  leadTime: null, url: "https://vend.com/p", suitability: 90, confidence: 0.9,
  pnMatchLevel: "exact", loc: "US", evidenceState: "priced",
};

const COMPARISON = {
  id: RUN,
  phase: "comparison",
  asset_specs: { manufacturer: "SKF", part_number: "6205" },
  sourcing_results: { tier1: [CANDIDATE], tier2: [], tier3: [] },
  approval_history: [],
  selected_candidate: null,
};

function stubRunApi() {
  return stubFetch((call) => {
    if (call.url.endsWith(`/api/runs/${RUN}`)) return jsonResponse(200, COMPARISON);
    if (call.url.includes(`/api/runs/${RUN}/orders`)) return jsonResponse(200, { run_id: RUN, count: 0, orders: [] });
    if (call.url.includes(`/api/runs/${RUN}/review-items`)) {
      return jsonResponse(200, { run_id: RUN, review_items: [], sent_count: 0, quote_count: 0 });
    }
    if (call.url.includes("/api/health")) return jsonResponse(200, { status: "ok", demo_mode: false });
    return jsonResponse(200, {});
  });
}

// ---------------------------------------------------------------------------
// 1. Controls hidden by permission
// ---------------------------------------------------------------------------

describe("approve controls follow the matrix", () => {
  it("a Requester sees no approve or reject control", () => {
    asRole("REQUESTER", <ApprovalActions run={AWAITING} />);
    expect(screen.queryByRole("button", { name: /approve/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /reject/i })).toBeNull();
    expect(screen.getByTestId("approval-no-rights")).toBeTruthy();
  });

  it("an Approver sees them, with no typed-name field (identity is the session)", () => {
    asRole("APPROVER", <ApprovalActions run={AWAITING} />);
    expect(screen.getByRole("button", { name: /approve/i })).toBeTruthy();
    expect(screen.queryByPlaceholderText("Your name")).toBeNull();
    expect(screen.getByText("approver@bayfoods.com")).toBeTruthy();
  });

  it("flag off (no session): today's typed-name approval, unchanged", () => {
    asRole(null, <ApprovalActions run={AWAITING} />);
    expect(screen.getByPlaceholderText("Your name")).toBeTruthy();
    expect(screen.getByRole("button", { name: /approve/i })).toBeTruthy();
  });
});

describe("select / order controls follow the matrix", () => {
  const ORDER = /^(order|get quote)/i;

  it("a Requester sees the options but no order or quote control", async () => {
    stubRunApi();
    asRole("REQUESTER", <OptionsScreen runId={RUN} />);
    await screen.findByText("Vend");
    expect(screen.queryAllByRole("button").filter((b) => ORDER.test(b.textContent ?? ""))).toEqual([]);
  });

  it("a Buyer sees the order control", async () => {
    stubRunApi();
    asRole("BUYER", <OptionsScreen runId={RUN} />);
    await screen.findByText("Vend");
    expect(screen.getAllByRole("button").some((b) => ORDER.test(b.textContent ?? ""))).toBe(true);
  });

  it("flag off (no session): the order control renders as today", async () => {
    stubRunApi();
    asRole(null, <OptionsScreen runId={RUN} />);
    await screen.findByText("Vend");
    expect(screen.getAllByRole("button").some((b) => ORDER.test(b.textContent ?? ""))).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// 2. The server decides: a hidden control's endpoint still 403s
// ---------------------------------------------------------------------------

describe("hiding is display only", () => {
  it("calling the approve endpoint directly sends the cookie and surfaces the server's 403", async () => {
    const calls = stubFetch(() => jsonResponse(403, { detail: "Forbidden" }));
    let caught: unknown;
    try {
      await approveRun(RUN, { approver_name: "x", approver_role: "y" });
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).status).toBe(403);
    expect(calls[0].init?.credentials).toBe("include");
  });
});

// ---------------------------------------------------------------------------
// 3. Nothing JS-readable after a full login → verify → act → logout cycle
// ---------------------------------------------------------------------------

describe("no session value in JS-readable storage", () => {
  it("after the complete cycle", async () => {
    vi.stubEnv(FLAG, "1");
    const spies = spyConsole();
    const calls = stubFetch((call) => {
      if (call.url.includes("/api/buyer/auth/request-link")) return jsonResponse(200, { ok: true });
      if (call.url.includes("/api/buyer/auth/verify")) {
        return new Response(JSON.stringify({ ok: true, expires_at: "2026-09-27T00:00:00Z" }), {
          status: 200,
          headers: {
            "content-type": "application/json",
            "set-cookie": `gofer_buyer_session=${SESSION_SECRET}; HttpOnly; Secure; SameSite=Lax; Path=/`,
          },
        });
      }
      if (call.url.includes("/api/buyer/me")) return jsonResponse(200, me("BUYER"));
      if (call.url.includes("/api/buyer/auth/logout")) return jsonResponse(200, { ok: true });
      return jsonResponse(200, { count: 0, events: [] });
    });

    // login
    const login = render(<BuyerLoginScreen />);
    fireEvent.change(screen.getByLabelText("Work email"), { target: { value: "buyer@bayfoods.com" } });
    fireEvent.click(screen.getByRole("button", { name: /email me a sign-in link/i }));
    await screen.findByText("Check your email");
    login.unmount();

    // verify — an explicit click, then home
    search = new URLSearchParams({ token: LINK_TOKEN });
    const verify = render(<BuyerVerifyScreen />);
    fireEvent.click(screen.getByRole("button", { name: CONTINUE_LABEL }));
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
    verify.unmount();

    // act — the signed-in shell loads, then logout
    withClient(<ProcShell><p>home</p></ProcShell>);
    await screen.findByText("home");
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("/auth/logout"))).toBe(true));

    for (const secret of [LINK_TOKEN, SESSION_SECRET]) {
      expect(storageDump(window.localStorage)).not.toContain(secret);
      expect(storageDump(window.sessionStorage)).not.toContain(secret);
      expect(document.cookie).not.toContain(secret);
      expect(consoleDump(spies)).not.toContain(secret);
    }
    // Every buyer call rode the cookie; none carried a credential of its own.
    for (const c of calls.filter((x) => x.url.includes("/api/buyer/"))) {
      expect(c.init?.credentials).toBe("include");
      expect(new Headers(c.init?.headers).get("authorization")).toBeNull();
    }
  });
});

// ---------------------------------------------------------------------------
// 4. Header, guard, verify gesture
// ---------------------------------------------------------------------------

describe("the buyer shell", () => {
  it("shows the authenticated company, not the fixture", async () => {
    vi.stubEnv(FLAG, "1");
    stubFetch((call) =>
      call.url.includes("/api/buyer/me") ? jsonResponse(200, me("ADMIN"))
        : jsonResponse(200, { count: 0, events: [] }));
    withClient(<ProcShell><p>home</p></ProcShell>);
    await screen.findByText("home");
    expect(screen.getByTestId("proc-tenant").textContent).toBe("Bay Foods");
    expect(screen.getByRole("button", { name: "Team" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Approval policy" })).toBeTruthy();
  });

  it("hides the Admin screens from a non-Admin", async () => {
    vi.stubEnv(FLAG, "1");
    stubFetch((call) =>
      call.url.includes("/api/buyer/me") ? jsonResponse(200, me("BUYER"))
        : jsonResponse(200, { count: 0, events: [] }));
    withClient(<ProcShell><p>home</p></ProcShell>);
    await screen.findByText("home");
    expect(screen.queryByRole("button", { name: "Team" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Approval policy" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delivery settings" })).toBeNull();
  });

  it("sends a signed-out visitor to /login and renders nothing", async () => {
    vi.stubEnv(FLAG, "1");
    stubFetch(() => unauthorized());
    withClient(<ProcShell><p>home</p></ProcShell>);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
    expect(screen.queryByText("home")).toBeNull();
  });

  it("flag off: no login, the fixture header, no /me call", async () => {
    const calls = stubFetch(() => jsonResponse(200, { count: 0, events: [] }));
    withClient(<ProcShell><p>home</p></ProcShell>);
    expect(screen.getByText("home")).toBeTruthy();
    expect(screen.getByTestId("proc-tenant").textContent).toBe("Northgate");
    expect(calls.some((c) => c.url.includes("/api/buyer/"))).toBe(false);
    expect(screen.queryByRole("button", { name: "Team" })).toBeNull();
  });

  it("verify does nothing until the explicit click", async () => {
    vi.stubEnv(FLAG, "1");
    search = new URLSearchParams({ token: LINK_TOKEN });
    const calls = stubFetch(() => jsonResponse(200, { ok: true }));
    render(<BuyerVerifyScreen />);
    await new Promise((r) => setTimeout(r, 20));
    expect(calls).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: CONTINUE_LABEL }));
    await waitFor(() => expect(calls.length).toBe(1));
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ token: LINK_TOKEN });
  });
});
