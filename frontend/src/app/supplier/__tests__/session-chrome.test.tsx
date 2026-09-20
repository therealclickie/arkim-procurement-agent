// @vitest-environment jsdom
/**
 * Arc 3 T10 — sign-out and the authenticated shell.
 *
 * Two claims:
 *
 *  1. SIGN-OUT IS A SERVER CALL. The credential is an httpOnly cookie that no
 *     script can delete, so "log out" means "ask the server to revoke the
 *     session". Clearing client state without that call would be theatre —
 *     the cookie would still authenticate the next request.
 *  2. A PROTECTED ROUTE WITHOUT A SESSION GOES TO LOGIN, for all three of
 *     them. The redirect is a courtesy, not the control: every one of these
 *     routes 401s server-side regardless of what the browser decides.
 *
 * And the full-cycle hygiene sweep the arc requires runs after
 * login → act → SIGN OUT, not merely after login.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SupplierRequestsPage from "../requests/page";
import SupplierProfilePage from "../profile/page";
import {
  consoleDump,
  jsonResponse,
  notFound,
  spyConsole,
  storageDump,
  stubFetch,
  unauthorized,
  type FetchCall,
} from "@/test-support/fetch-seam";
import {
  openRequest,
  portalProfile,
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
  window.localStorage.clear();
  window.sessionStorage.clear();
});

/** A signed-in supplier with one open request. */
function stubSignedIn(
  logout: () => Response = () => jsonResponse(200, { ok: true }),
): FetchCall[] {
  return stubFetch((call) => {
    if (call.url === "/api/supplier/me") return jsonResponse(200, supplierMe());
    if (call.url === "/api/supplier/auth/logout") return logout();
    if (call.url === "/api/supplier/requests") {
      return jsonResponse(200, { requests: [openRequest()] });
    }
    if (call.url === "/api/supplier/quotes") {
      return jsonResponse(200, { quotes: [] });
    }
    if (call.url === "/api/supplier/profile") {
      return jsonResponse(200, portalProfile());
    }
    return notFound();
  });
}

// ---------------------------------------------------------------------------
// The shell
// ---------------------------------------------------------------------------

describe("session chrome", () => {
  it("shows the account, the member and a sign-out control", async () => {
    stubSignedIn();
    render(<SupplierRequestsPage />);
    await screen.findByText("Open requests for you");
    expect(screen.getByText("sealit.example.com")).toBeTruthy();
    expect(screen.getByText("sales@sealit.example.com")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeTruthy();
  });

  it("marks the current section and links to the others", async () => {
    stubSignedIn();
    render(<SupplierRequestsPage />);
    await screen.findByText("Open requests for you");
    const requests = screen.getByRole("link", { name: "Requests" });
    expect(requests.getAttribute("aria-current")).toBe("page");
    expect(
      screen.getByRole("link", { name: "Profile" }).getAttribute("href"),
    ).toBe("/supplier/profile");
  });
});

// ---------------------------------------------------------------------------
// Sign-out
// ---------------------------------------------------------------------------

describe("sign-out", () => {
  it("calls the logout endpoint and redirects to login", async () => {
    const calls = stubSignedIn();
    const user = userEvent.setup();
    render(<SupplierRequestsPage />);
    await screen.findByText("Open requests for you");
    await user.click(screen.getByRole("button", { name: "Sign out" }));

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/supplier/login"));
    const logout = calls.find((c) => c.url === "/api/supplier/auth/logout");
    expect(logout).toBeTruthy();
    expect(logout!.init?.method).toBe("POST");
    expect(logout!.init?.credentials).toBe("include");
  });

  it("leaves anyway if the logout call fails", async () => {
    // Keeping a supplier on an authenticated screen after they asked to leave
    // would read as "still signed in" — the dangerous reading on a shared
    // machine. The server-side revoke is the control; this is the
    // acknowledgement.
    stubSignedIn(() => jsonResponse(500, { detail: "boom" }));
    const user = userEvent.setup();
    render(<SupplierRequestsPage />);
    await screen.findByText("Open requests for you");
    await user.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/supplier/login"));
  });

  it("does not attempt to delete the cookie in JS", async () => {
    // It could not succeed — the cookie is httpOnly — and trying would
    // suggest the client believes it is the one revoking the session.
    const calls = stubSignedIn();
    const user = userEvent.setup();
    render(<SupplierRequestsPage />);
    await screen.findByText("Open requests for you");
    await user.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(replace).toHaveBeenCalled());
    expect(document.cookie).toBe("");
    expect(calls.some((c) => c.url === "/api/supplier/auth/logout")).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Full-cycle hygiene — login -> act -> SIGN OUT
// ---------------------------------------------------------------------------

describe("full cycle — nothing is persisted or logged", () => {
  it("sweeps clean after load, interaction and sign-out", async () => {
    const spies = spyConsole();
    const calls = stubSignedIn();
    const user = userEvent.setup();
    render(<SupplierRequestsPage />);
    await screen.findByText("Open requests for you");
    // Act: open a quote form, then leave.
    await user.click(screen.getByRole("button", { name: "Quote this" }));
    await screen.findByLabelText("Your quote / reference number");
    await user.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/supplier/login"));

    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
    expect(storageDump(window.localStorage)).toBe("");
    expect(storageDump(window.sessionStorage)).toBe("");
    expect(document.cookie).toBe("");
    expect(consoleDump(spies)).toBe("");
    // And no request ever carried an Authorization header.
    for (const c of calls) {
      expect(new Headers(c.init?.headers).get("authorization")).toBeNull();
    }
  });
});

// ---------------------------------------------------------------------------
// Protected routes without a session
// ---------------------------------------------------------------------------

describe("protected routes without a session", () => {
  const routes: Array<[string, () => React.ReactElement]> = [
    ["/supplier/requests", () => <SupplierRequestsPage />],
    ["/supplier/profile", () => <SupplierProfilePage />],
  ];

  for (const [name, Page] of routes) {
    it(`${name} redirects to login and renders nothing`, async () => {
      stubFetch(() => unauthorized());
      const { container } = render(Page());
      await waitFor(() =>
        expect(replace).toHaveBeenCalledWith("/supplier/login"),
      );
      expect(container.textContent).toBe("");
    });
  }

  it("a 401 on a LATER call also lands the supplier at login", async () => {
    // The session can expire mid-visit. The mode reports the 401, the guard
    // re-checks, and the redirect follows — the page never sits there
    // silently failing to load.
    let meCalls = 0;
    stubFetch((call) => {
      if (call.url === "/api/supplier/me") {
        meCalls += 1;
        return meCalls === 1
          ? jsonResponse(200, supplierMe())
          : unauthorized();
      }
      return unauthorized();   // requests/quotes have expired
    });
    render(<SupplierRequestsPage />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/supplier/login"));
  });
});
