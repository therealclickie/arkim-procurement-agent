// @vitest-environment jsdom
/**
 * T3 — security posture of the PUBLIC claim-portal route (/portal/[token]).
 * The token is the credential: after a FULL render + submit cycle it must
 * never be persisted (storage/cookie), never logged (console), and the page
 * must never call anything outside /api/portal/* — in particular no
 * /api/admin/* path and no Authorization header (T7.4 cross-check).
 *
 * All checks inspect real observable state after real interaction — not mere
 * render success.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ClaimPage } from "../claim-page";
import { stubFetch, jsonResponse, notFound, type FetchCall } from "@/test-support/fetch-seam";
import { portalProfile } from "@/test-support/fixtures";

const TOKEN = "claim_tok_9f3k2m";
const PROFILE_URL = `/api/portal/${TOKEN}/profile`;
const REVISION_URL = `/api/portal/${TOKEN}/propose-revision`;

function stubLivePortal() {
  return stubFetch((call) => {
    if (call.url === PROFILE_URL && (call.init?.method ?? "GET") === "GET") {
      return jsonResponse(200, portalProfile());
    }
    if (call.url === REVISION_URL && call.init?.method === "POST") {
      return jsonResponse(200, { ok: true, revision_id: "rev_001", status: "pending" });
    }
    return notFound(); // Night-11 quote endpoints (feature off)
  });
}

/** Every value the page could have persisted, concatenated for a substring sweep. */
function storageDump(store: Storage): string {
  const parts: string[] = [];
  for (let i = 0; i < store.length; i++) {
    const key = store.key(i);
    if (key !== null) parts.push(key, store.getItem(key) ?? "");
  }
  return parts.join("\n");
}

function spyConsole(): Array<vi.Spied<typeof console.log>> {
  return (["log", "info", "warn", "error", "debug"] as const).map((m) =>
    vi.spyOn(console, m).mockImplementation(() => {}),
  );
}

function consoleDump(spies: Array<vi.Spied<typeof console.log>>): string {
  return spies
    .flatMap((s) => s.mock.calls)
    .map((args) => args.map((a) => (typeof a === "string" ? a : JSON.stringify(a))).join(" "))
    .join("\n");
}

function headerValue(call: FetchCall, name: string): string | undefined {
  const h = call.init?.headers as Record<string, string> | undefined;
  return h?.[name];
}

describe("portal route — token hygiene (full render + submit cycle)", () => {
  it("leaves no substring of the token in localStorage, sessionStorage, or cookies", async () => {
    const spies = spyConsole();
    stubLivePortal();
    const user = userEvent.setup();
    render(<ClaimPage token={TOKEN} />);
    await screen.findByText("Confirm your profile");
    await user.click(screen.getByRole("button", { name: "Submit for review" }));
    await screen.findByText("Submitted for review");

    // After the complete cycle, sweep every persisted surface.
    expect(storageDump(window.localStorage)).not.toContain(TOKEN);
    expect(storageDump(window.sessionStorage)).not.toContain(TOKEN);
    expect(document.cookie).not.toContain(TOKEN);
    spies.forEach((s) => s.mockRestore());
  });

  it("never writes the token to any console.* output", async () => {
    const spies = spyConsole();
    stubLivePortal();
    const user = userEvent.setup();
    render(<ClaimPage token={TOKEN} />);
    await screen.findByText("Confirm your profile");
    await user.click(screen.getByRole("button", { name: "Submit for review" }));
    await screen.findByText("Submitted for review");

    expect(consoleDump(spies)).not.toContain(TOKEN);
    spies.forEach((s) => s.mockRestore());
  });
});

describe("portal route — network reach (fetch-seam URL/header assertions)", () => {
  it("calls ONLY /api/portal/* — never an admin endpoint", async () => {
    const calls = stubLivePortal();
    const user = userEvent.setup();
    render(<ClaimPage token={TOKEN} />);
    await screen.findByText("Confirm your profile");
    await user.click(screen.getByRole("button", { name: "Submit for review" }));
    await screen.findByText("Submitted for review");

    expect(calls.length).toBeGreaterThan(0);
    const urls = calls.map((c) => c.url);
    expect(urls.every((u) => /^\/api\/portal\//.test(u))).toBe(true);
    expect(urls.some((u) => u.includes("/api/admin"))).toBe(false);
  });

  it("sends no Authorization header on any public-portal call (no admin material)", async () => {
    // T7.4 cross-check from the public side: the admin bearer token can never
    // ride along on a public route's requests.
    const calls = stubLivePortal();
    const user = userEvent.setup();
    render(<ClaimPage token={TOKEN} />);
    await screen.findByText("Confirm your profile");
    await user.click(screen.getByRole("button", { name: "Submit for review" }));
    await screen.findByText("Submitted for review");

    for (const c of calls) {
      expect(headerValue(c, "Authorization")).toBeUndefined();
      expect(headerValue(c, "authorization")).toBeUndefined();
    }
  });
});
