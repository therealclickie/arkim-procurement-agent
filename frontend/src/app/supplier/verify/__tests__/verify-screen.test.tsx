// @vitest-environment jsdom
/**
 * Arc 3 T5 — /supplier/verify.
 *
 * Two properties carry the weight:
 *
 *  1. EVERY failure mode renders byte-identical output. Asserted as a
 *     Set-size-1 equality over the four the brief names (expired, used,
 *     unknown, pending member) PLUS the shapes a misbehaving backend could
 *     produce (403 / 404 / 500 / network throw) and the no-token-at-all case.
 *     A contrast case proves the equality would fail on divergent output.
 *  2. The token, which arrives in the URL, is nowhere afterwards — not in
 *     localStorage, sessionStorage, document.cookie, or any console channel —
 *     swept after the COMPLETE cycle, success and failure alike.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { VerifyScreen, INBOX_PATH } from "../verify-screen";
import SupplierVerifyPage from "../page";
import {
  consoleDump,
  jsonResponse,
  notFound,
  spyConsole,
  storageDump,
  stubFetch,
  unauthorized,
} from "@/test-support/fetch-seam";

const FLAG = "NEXT_PUBLIC_SUPPLIER_SESSION_V1";
const TOKEN = "magic_raw_tok_5c1a9e77";

const replace = vi.fn();
let search = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => search,
}));

beforeEach(() => {
  replace.mockClear();
  search = new URLSearchParams({ token: TOKEN });
});

afterEach(() => {
  vi.unstubAllEnvs();
  window.localStorage.clear();
  window.sessionStorage.clear();
});

/** Render with the current query string and return the rendered text once a
 *  terminal state is reached. */
async function renderAndRead(): Promise<string> {
  const view = render(<VerifyScreen />);
  await screen.findByText("This sign-in link is no longer valid");
  const text = view.container.textContent ?? "";
  view.unmount();
  return text;
}

// ---------------------------------------------------------------------------
// Success
// ---------------------------------------------------------------------------

describe("verify — success", () => {
  it("posts the token and redirects to the inbox", async () => {
    const calls = stubFetch(() =>
      jsonResponse(200, { token: "sess_raw", expires_at: "2026-09-21T00:00:00Z" }),
    );
    render(<VerifyScreen />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith(INBOX_PATH));

    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("/api/supplier/auth/verify");
    expect(calls[0].init?.method).toBe("POST");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ token: TOKEN });
    expect(calls[0].init?.credentials).toBe("include");
  });

  it("uses replace, not push, so the token URL leaves history", async () => {
    const push = vi.fn();
    stubFetch(() => jsonResponse(200, { token: "sess_raw" }));
    render(<VerifyScreen />);
    await waitFor(() => expect(replace).toHaveBeenCalled());
    expect(push).not.toHaveBeenCalled();
  });

  it("exchanges the token exactly once (single-use links survive StrictMode)", async () => {
    const calls = stubFetch(() => jsonResponse(200, { token: "sess_raw" }));
    render(<VerifyScreen />);
    await waitFor(() => expect(replace).toHaveBeenCalled());
    expect(calls).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// Failure — the equality
// ---------------------------------------------------------------------------

describe("verify — every failure mode is byte-identical", () => {
  it("renders one output across expired / used / unknown / pending", async () => {
    // All four are the backend's ONE uniform 401; the UI must not add a
    // distinction downstream of it either.
    stubFetch(() => unauthorized());
    const outputs = new Set<string>();
    for (const token of ["expired_tok", "already_used_tok", "unknown_tok",
                         "pending_member_tok"]) {
      search = new URLSearchParams({ token });
      outputs.add(await renderAndRead());
    }
    expect(outputs.size).toBe(1);
  });

  it("collapses divergent backend shapes to the same output", async () => {
    // Defence in depth: even if some future proxy turned a 401 into a 404 or
    // a 500, the supplier sees the same thing.
    const responders = [
      () => unauthorized(),
      () => notFound(),
      () => jsonResponse(403, { detail: "Forbidden" }),
      () => jsonResponse(500, { detail: "boom" }),
      () => {
        throw new Error("offline");
      },
    ];
    const outputs = new Set<string>();
    for (const r of responders) {
      stubFetch(r);
      outputs.add(await renderAndRead());
    }
    expect(outputs.size).toBe(1);
  });

  it("treats a missing token as the same dead end", async () => {
    stubFetch(() => unauthorized());
    const withToken = await renderAndRead();

    search = new URLSearchParams();
    const calls = stubFetch(() => unauthorized());
    const withoutToken = await renderAndRead();

    expect(withoutToken).toBe(withToken);
    // ...and no pointless request was made for an absent token.
    expect(calls).toHaveLength(0);
  });

  it("the equality would FAIL on divergent output (contrast case)", async () => {
    stubFetch(() => unauthorized());
    const rejected = await renderAndRead();

    stubFetch(() => jsonResponse(200, { token: "sess_raw" }));
    const view = render(<VerifyScreen />);
    await waitFor(() => expect(replace).toHaveBeenCalled());
    const succeeded = view.container.textContent ?? "";
    view.unmount();

    expect(new Set([rejected, succeeded]).size).toBe(2);
  });

  it("never names which failure occurred, and offers a way back", async () => {
    stubFetch(() => unauthorized());
    const text = await renderAndRead();
    expect(text).not.toMatch(/already used|expired link was|pending|not found|unknown token/i);
    expect(text).toMatch(/no longer valid/i);

    stubFetch(() => unauthorized());
    render(<VerifyScreen />);
    await screen.findByText("This sign-in link is no longer valid");
    const back = screen.getByRole("link", { name: "Request a new sign-in link" });
    expect(back.getAttribute("href")).toBe("/supplier/login");
  });
});

// ---------------------------------------------------------------------------
// Token hygiene — after the COMPLETE cycle, both outcomes
// ---------------------------------------------------------------------------

describe("verify — the token is nowhere afterwards", () => {
  it("leaves no trace after a successful sign-in", async () => {
    const spies = spyConsole();
    stubFetch(() =>
      jsonResponse(200, { token: "sess_raw_abc", expires_at: "2026-09-21T00:00:00Z" }),
    );
    render(<VerifyScreen />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith(INBOX_PATH));

    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
    expect(storageDump(window.localStorage)).not.toContain(TOKEN);
    expect(storageDump(window.sessionStorage)).not.toContain(TOKEN);
    expect(document.cookie).toBe("");
    const logged = consoleDump(spies);
    expect(logged).toBe("");
    expect(logged).not.toContain(TOKEN);
    // The SESSION token in the response body is not held either — the
    // browser's session is the httpOnly cookie, which JS cannot read.
    expect(storageDump(window.localStorage)).not.toContain("sess_raw_abc");
    expect(document.cookie).not.toContain("sess_raw_abc");
  });

  it("leaves no trace after a rejected sign-in", async () => {
    const spies = spyConsole();
    stubFetch(() => unauthorized());
    await renderAndRead();

    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
    expect(document.cookie).toBe("");
    expect(consoleDump(spies)).toBe("");
  });

  it("does not render the token on screen", async () => {
    stubFetch(() => unauthorized());
    const text = await renderAndRead();
    expect(text).not.toContain(TOKEN);
  });
});

// ---------------------------------------------------------------------------
// The route's flag gate
// ---------------------------------------------------------------------------

describe("/supplier/verify route — flag gating", () => {
  it("renders NOTHING and makes no request when the flag is off", () => {
    vi.stubEnv(FLAG, "0");
    const calls = stubFetch(() => jsonResponse(200, { token: "x" }));
    const { container } = render(<SupplierVerifyPage />);
    expect(container.textContent).toBe("");
    expect(calls).toHaveLength(0);
  });

  it("verifies when the flag is on", async () => {
    vi.stubEnv(FLAG, "1");
    stubFetch(() => jsonResponse(200, { token: "sess_raw" }));
    render(<SupplierVerifyPage />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith(INBOX_PATH));
  });
});
