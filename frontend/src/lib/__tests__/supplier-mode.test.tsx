// @vitest-environment jsdom
/**
 * Arc 3 T3 — the auth-mode seam and the session client.
 *
 * Three things are being pinned here:
 *
 *  1. TOKEN-MODE PARITY. Routing the claim surface through the mode object
 *     must not change one byte of the request it emits — same URL, same
 *     method, same headers, no credentials, no Authorization. Arc 1's
 *     unmodifiable tests assert this from the component side; these assert it
 *     at the seam, so a regression is localised rather than mysterious.
 *  2. SESSION MODE sends the cookie (`credentials: "include"`) and NOTHING
 *     else — in particular never an Authorization header, because a browser
 *     must not hold a bearer (D1).
 *  3. A 401 is surfaced to the caller ONCE, as a signal to navigate, and is
 *     collapsed to the components' uniform `rejected` so no component has to
 *     know about sessions.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  sessionMode,
  tokenMode,
  SupplierModeProvider,
  useSupplierMode,
} from "@/lib/supplier-mode";
import { useSupplierSession } from "@/lib/use-supplier-session";
import {
  requestMagicLink,
  verifyMagicLink,
} from "@/lib/supplier-api";
import {
  headerValue,
  jsonResponse,
  notFound,
  stubFetch,
  tooManyRequests,
  unauthorized,
} from "@/test-support/fetch-seam";
import {
  openRequest,
  portalProfile,
  quoteHistoryRow,
  supplierMe,
} from "@/test-support/fixtures";

const TOKEN = "claim_tok_seam_1";

const QUOTE_BODY = {
  run_id: "run_001",
  quote_number: "Q-1",
  unit_price: 1250,
  quantity: 2,
  lead_time: "in stock",
};

afterEach(() => {
  vi.unstubAllEnvs();
});

// ---------------------------------------------------------------------------
// 1. Token-mode parity
// ---------------------------------------------------------------------------

describe("token mode — byte-identical to calling portal-api directly", () => {
  it("emits the same URL and method for every operation", async () => {
    const calls = stubFetch(() => jsonResponse(200, {}));
    const mode = tokenMode(TOKEN);
    await mode.getProfile();
    await mode.getOpenRequests();
    await mode.getQuoteHistory();
    await mode.proposeRevision({});
    await mode.submitQuote(QUOTE_BODY);

    expect(calls.map((c) => [c.init?.method ?? "GET", c.url])).toEqual([
      ["GET", `/api/portal/${TOKEN}/profile`],
      ["GET", `/api/portal/${TOKEN}/open-requests`],
      ["GET", `/api/portal/${TOKEN}/quotes`],
      ["POST", `/api/portal/${TOKEN}/propose-revision`],
      ["POST", `/api/portal/${TOKEN}/quotes`],
    ]);
  });

  it("sends NO credentials and NO Authorization on any token call", async () => {
    // The public surface has no ambient credential and must never acquire
    // one — adding `credentials` to the shared primitive would have done
    // exactly that, silently, on every claim-page request.
    const calls = stubFetch(() => jsonResponse(200, {}));
    const mode = tokenMode(TOKEN);
    await mode.getProfile();
    await mode.submitQuote(QUOTE_BODY);
    for (const c of calls) {
      expect(c.init?.credentials).toBeUndefined();
      expect(headerValue(c, "authorization")).toBeNull();
      expect(c.init?.cache).toBe("no-store");
    }
  });

  it("percent-encodes the token exactly as before", async () => {
    const calls = stubFetch(() => jsonResponse(200, {}));
    await tokenMode("a/b c").getProfile();
    expect(calls[0].url).toBe("/api/portal/a%2Fb%20c/profile");
  });

  it("collapses any non-200 to the uniform rejection", async () => {
    stubFetch(() => notFound());
    const res = await tokenMode(TOKEN).getProfile();
    expect(res).toEqual({ ok: false, rejected: true });
  });
});

// ---------------------------------------------------------------------------
// 2. Session mode
// ---------------------------------------------------------------------------

describe("session mode — the cookie is the whole credential", () => {
  it("maps every operation onto /api/supplier/*", async () => {
    const calls = stubFetch((call) => {
      if (call.url === "/api/supplier/profile") {
        return jsonResponse(200, portalProfile());
      }
      if (call.url === "/api/supplier/requests") {
        return jsonResponse(200, { requests: [openRequest()] });
      }
      if (call.url === "/api/supplier/quotes" &&
          (call.init?.method ?? "GET") === "GET") {
        return jsonResponse(200, { quotes: [quoteHistoryRow()] });
      }
      return jsonResponse(200, { ok: true });
    });
    const mode = sessionMode();
    await mode.getProfile();
    await mode.getOpenRequests();
    await mode.getQuoteHistory();
    await mode.proposeRevision({});
    await mode.submitQuote(QUOTE_BODY);

    expect(calls.map((c) => [c.init?.method ?? "GET", c.url])).toEqual([
      ["GET", "/api/supplier/profile"],
      ["GET", "/api/supplier/requests"],
      ["GET", "/api/supplier/quotes"],
      ["POST", "/api/supplier/propose-revision"],
      ["POST", "/api/supplier/quotes"],
    ]);
    // Never a token path, never an admin path.
    expect(calls.every((c) => c.url.startsWith("/api/supplier/"))).toBe(true);
  });

  it("sends credentials and never an Authorization header", async () => {
    const calls = stubFetch(() => jsonResponse(200, { requests: [] }));
    await sessionMode().getOpenRequests();
    await sessionMode().submitQuote(QUOTE_BODY);
    expect(calls.length).toBe(2);
    for (const c of calls) {
      expect(c.init?.credentials).toBe("include");
      expect(headerValue(c, "authorization")).toBeNull();
      expect(c.init?.cache).toBe("no-store");
    }
  });

  it("returns the data unwrapped into the components' PortalResult", async () => {
    stubFetch(() => jsonResponse(200, { requests: [openRequest()] }));
    const res = await sessionMode().getOpenRequests();
    expect(res.ok).toBe(true);
    if (res.ok) expect(res.data.requests).toHaveLength(1);
  });

  it("signals a 401 to the caller and still reports a uniform rejection", async () => {
    stubFetch(() => unauthorized());
    const onUnauthorized = vi.fn();
    const res = await sessionMode({ onUnauthorized }).getOpenRequests();
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    expect(res).toEqual({ ok: false, rejected: true });
  });

  it("does NOT signal unauthorized for an ordinary failure", async () => {
    // A 404 (feature off) must not evict a perfectly good session.
    stubFetch(() => notFound());
    const onUnauthorized = vi.fn();
    const res = await sessionMode({ onUnauthorized }).getQuoteHistory();
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(res).toEqual({ ok: false, rejected: true });
  });

  it("treats a network throw as a rejection, not a logout", async () => {
    stubFetch(() => {
      throw new Error("offline");
    });
    const onUnauthorized = vi.fn();
    const res = await sessionMode({ onUnauthorized }).getProfile();
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(res).toEqual({ ok: false, rejected: true });
  });
});

// ---------------------------------------------------------------------------
// 3. The context default — the property that keeps arc 1's tests green
// ---------------------------------------------------------------------------

function ModeProbe({ token }: { token?: string }) {
  const mode = useSupplierMode(token);
  return <span data-testid="kind">{mode.kind}</span>;
}

describe("useSupplierMode — token mode is the default, session mode is opt-in", () => {
  it("falls back to token mode outside any provider", () => {
    render(<ModeProbe token={TOKEN} />);
    expect(screen.getByTestId("kind").textContent).toBe("token");
  });

  it("uses the provided mode inside a provider", () => {
    render(
      <SupplierModeProvider mode={sessionMode()}>
        <ModeProbe />
      </SupplierModeProvider>,
    );
    expect(screen.getByTestId("kind").textContent).toBe("session");
  });

  it("the fallback really calls the token endpoint", async () => {
    // Proves the default is a WORKING token mode, not merely a label.
    const calls = stubFetch(() => jsonResponse(200, {}));
    function Caller() {
      const mode = useSupplierMode(TOKEN);
      return (
        <button onClick={() => void mode.getProfile()}>go</button>
      );
    }
    render(<Caller />);
    await userEvent.click(screen.getByRole("button", { name: "go" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].url).toBe(`/api/portal/${TOKEN}/profile`);
  });
});

// ---------------------------------------------------------------------------
// 4. The auth endpoints
// ---------------------------------------------------------------------------

describe("auth endpoints — no enumeration oracle survives the client", () => {
  it("requestMagicLink reports success identically for every 200", async () => {
    stubFetch(() => jsonResponse(200, { ok: true }));
    const results = await Promise.all([
      requestMagicLink("known@dxpe.com"),
      requestMagicLink("unknown@nowhere.example"),
      requestMagicLink("garbage"),
    ]);
    expect(new Set(results.map((r) => JSON.stringify(r))).size).toBe(1);
    expect(results[0]).toEqual({ ok: true });
  });

  it("requestMagicLink distinguishes only the rate limit, never existence", async () => {
    stubFetch(() => tooManyRequests());
    expect(await requestMagicLink("anyone@dxpe.com")).toEqual({
      ok: false,
      rateLimited: true,
    });
  });

  it("verifyMagicLink returns a bare boolean and never surfaces the token", async () => {
    // The response body carries a raw bearer for API clients. A browser must
    // not hold it — so the client's return type makes holding it impossible.
    const calls = stubFetch(() =>
      jsonResponse(200, { token: "raw_session_tok", expires_at: "2026-09-21T00:00:00Z" }),
    );
    const result = await verifyMagicLink("magic_tok_1");
    expect(result).toBe(true);
    expect(typeof result).toBe("boolean");
    expect(calls[0].url).toBe("/api/supplier/auth/verify");
    expect(calls[0].init?.credentials).toBe("include");
  });

  it("verifyMagicLink returns false for every failure mode alike", async () => {
    stubFetch(() => unauthorized());
    expect(await verifyMagicLink("expired")).toBe(false);
    expect(await verifyMagicLink("used")).toBe(false);
    expect(await verifyMagicLink("unknown")).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// 5. useSupplierSession
// ---------------------------------------------------------------------------

function SessionProbe() {
  const { account, member, permissions, loading, unauthorized: unauth, can, logout } =
    useSupplierSession();
  if (loading) return <p>loading</p>;
  if (unauth) return <p>no session</p>;
  return (
    <div>
      <span data-testid="domain">{account?.supplier_domain}</span>
      <span data-testid="email">{member?.email}</span>
      <span data-testid="perms">{permissions.join(",")}</span>
      <span data-testid="can-manage">{String(can("manage_members"))}</span>
      <button onClick={() => void logout()}>sign out</button>
    </div>
  );
}

describe("useSupplierSession", () => {
  it("loads the account, member and permissions from /me", async () => {
    stubFetch(() => jsonResponse(200, supplierMe()));
    render(<SessionProbe />);
    await screen.findByTestId("domain");
    expect(screen.getByTestId("domain").textContent).toBe("sealit.example.com");
    expect(screen.getByTestId("email").textContent).toBe("sales@sealit.example.com");
    expect(screen.getByTestId("can-manage").textContent).toBe("true");
  });

  it("reflects a MEMBER's narrower permissions", async () => {
    stubFetch(() =>
      jsonResponse(200, supplierMe({
        member: { id: "m2", email: "m@x.com", role: "MEMBER", status: "ACTIVE",
                  permissions: ["view_requests", "submit_quotes",
                                "propose_revisions", "view_members"] },
      })),
    );
    render(<SessionProbe />);
    await screen.findByTestId("can-manage");
    expect(screen.getByTestId("can-manage").textContent).toBe("false");
  });

  it("reports unauthorized on a 401 and holds no identity", async () => {
    stubFetch(() => unauthorized());
    render(<SessionProbe />);
    expect(await screen.findByText("no session")).toBeTruthy();
  });

  it("clears session state on logout even if the call fails", async () => {
    // The cookie is httpOnly, so the client cannot confirm it is gone. Leaving
    // an identity on screen after the user asked to leave is the worse failure.
    let meCount = 0;
    stubFetch((call) => {
      if (call.url === "/api/supplier/me") {
        meCount += 1;
        return jsonResponse(200, supplierMe());
      }
      return jsonResponse(500, { detail: "boom" });
    });
    render(<SessionProbe />);
    await screen.findByTestId("domain");
    await userEvent.click(screen.getByRole("button", { name: "sign out" }));
    expect(await screen.findByText("no session")).toBeTruthy();
    expect(meCount).toBe(1);
  });

  it("calls the logout endpoint with credentials", async () => {
    const calls = stubFetch((call) =>
      call.url === "/api/supplier/me"
        ? jsonResponse(200, supplierMe())
        : jsonResponse(200, { ok: true }),
    );
    render(<SessionProbe />);
    await screen.findByTestId("domain");
    await userEvent.click(screen.getByRole("button", { name: "sign out" }));
    await waitFor(() =>
      expect(calls.some((c) => c.url === "/api/supplier/auth/logout")).toBe(true),
    );
    const logoutCall = calls.find((c) => c.url === "/api/supplier/auth/logout")!;
    expect(logoutCall.init?.method).toBe("POST");
    expect(logoutCall.init?.credentials).toBe("include");
  });
});
