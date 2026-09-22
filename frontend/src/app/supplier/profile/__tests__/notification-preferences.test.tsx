// @vitest-environment jsdom
/**
 * Arc 4 T11 / D7 — the notification-preference control on /supplier/profile.
 *
 * WHAT IS BEING PINNED
 *  1. Flag off ⇒ the surface does not exist. Not "renders disabled", not
 *     "renders and 404s" — absent, and the profile page is as it was.
 *  2. The preference round-trips over the real network seam, so the URL and
 *     the method are observable facts (PUT /api/supplier/notification-
 *     preferences), not a mocked module's say-so.
 *  3. A FAILED save reverts the control. A radio left on the new value after a
 *     failed write tells the supplier they turned notifications down when they
 *     did not — the one outcome this screen must never produce.
 *  4. Nothing is written to localStorage / sessionStorage (the arc 1/3 sweep).
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SupplierProfilePage from "../page";
import {
  jsonResponse,
  notFound,
  storageDump,
  stubFetch,
  unauthorized,
  type FetchCall,
} from "@/test-support/fetch-seam";
import { portalProfile, supplierMe } from "@/test-support/fixtures";

const SESSION_FLAG = "NEXT_PUBLIC_SUPPLIER_SESSION_V1";
const NOTIFY_FLAG = "NEXT_PUBLIC_NOTIFICATIONS_V1";
const PREFS_URL = "/api/supplier/notification-preferences";
const CHOICES = ["IMMEDIATE", "DAILY_DIGEST", "NONE"];

const replace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/supplier/profile",
}));

beforeEach(() => {
  replace.mockClear();
  vi.stubEnv(SESSION_FLAG, "1");
  vi.stubEnv(NOTIFY_FLAG, "1");
  window.localStorage.clear();
  window.sessionStorage.clear();
});

afterEach(() => {
  vi.unstubAllEnvs();
});

/** A backend with a session, a profile, and the preference endpoint. */
function stubProfile({
  preference = "IMMEDIATE",
  prefsStatus = 200,
  putStatus = 200,
}: {
  preference?: string;
  prefsStatus?: number;
  putStatus?: number;
} = {}): FetchCall[] {
  return stubFetch((call) => {
    if (call.url === "/api/supplier/me") return jsonResponse(200, supplierMe());
    if (call.url === "/api/supplier/profile") {
      return jsonResponse(200, portalProfile());
    }
    if (call.url === PREFS_URL) {
      const method = call.init?.method ?? "GET";
      if (method === "PUT") {
        if (putStatus !== 200) return jsonResponse(putStatus, { detail: "nope" });
        const body = JSON.parse(String(call.init?.body ?? "{}"));
        return jsonResponse(200, { ok: true, preference: body.preference });
      }
      if (prefsStatus === 404) return notFound();
      if (prefsStatus === 401) return unauthorized();
      return jsonResponse(200, { preference, choices: CHOICES });
    }
    return notFound();
  });
}

/** The preference radios only. The profile FORM on this page has radios of its
 *  own (the tri-state brand control), so every query here is scoped to the
 *  section — an unscoped getAllByRole("radio") would silently include them. */
const prefSection = () => screen.getByLabelText("Email notifications");
const radio = (name: string | RegExp) =>
  within(prefSection()).getByRole("radio", { name });
const prefRadios = () => within(prefSection()).getAllByRole("radio");

// ---------------------------------------------------------------------------
// Flag posture
// ---------------------------------------------------------------------------

describe("notification preferences — flag posture", () => {
  it("renders nothing when NEXT_PUBLIC_NOTIFICATIONS_V1 is off", async () => {
    vi.stubEnv(NOTIFY_FLAG, "");
    const calls = stubProfile();
    render(<SupplierProfilePage />);
    await screen.findByText("Confirm your profile");
    expect(screen.queryByLabelText("Email notifications")).toBeNull();
    expect(
      screen.queryByRole("radio", { name: /request emails/i }),
    ).toBeNull();
    expect(calls.some((c) => c.url === PREFS_URL)).toBe(false);
  });

  it("renders nothing when the backend flag is off (the endpoint 404s)", async () => {
    const calls = stubProfile({ prefsStatus: 404 });
    render(<SupplierProfilePage />);
    await screen.findByText("Confirm your profile");
    await waitFor(() =>
      expect(calls.some((c) => c.url === PREFS_URL)).toBe(true),
    );
    expect(screen.queryByLabelText("Email notifications")).toBeNull();
  });

  it("renders nothing when the session has expired", async () => {
    stubProfile({ prefsStatus: 401 });
    render(<SupplierProfilePage />);
    await screen.findByText("Confirm your profile");
    expect(screen.queryByLabelText("Email notifications")).toBeNull();
  });

  it("does not disturb the rest of the profile screen", async () => {
    stubProfile();
    render(<SupplierProfilePage />);
    await screen.findByText("Confirm your profile");
    await screen.findByLabelText("Email notifications");
    expect(
      screen.getByRole("button", { name: "Submit for review" }),
    ).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Reading and writing
// ---------------------------------------------------------------------------

describe("notification preferences — round trip", () => {
  it("shows the server's current value selected", async () => {
    stubProfile({ preference: "DAILY_DIGEST" });
    render(<SupplierProfilePage />);
    await screen.findByLabelText("Email notifications");
    expect((radio(/one summary email a day/i) as HTMLInputElement).checked).toBe(
      true,
    );
    expect(
      (radio(/as soon as a request arrives/i) as HTMLInputElement).checked,
    ).toBe(false);
  });

  it("renders one option per server-supplied choice, not a client-side list", async () => {
    stubProfile();
    render(<SupplierProfilePage />);
    await screen.findByLabelText("Email notifications");
    expect(prefRadios()).toHaveLength(CHOICES.length);
  });

  it("PUTs the chosen value to the session endpoint and confirms", async () => {
    const calls = stubProfile();
    const user = userEvent.setup();
    render(<SupplierProfilePage />);
    await screen.findByLabelText("Email notifications");
    await user.click(radio(/no request emails/i));

    await screen.findByText("Saved");
    const put = calls.find(
      (c) => c.url === PREFS_URL && c.init?.method === "PUT",
    );
    expect(put).toBeTruthy();
    expect(JSON.parse(String(put?.init?.body))).toEqual({ preference: "NONE" });
    expect((radio(/no request emails/i) as HTMLInputElement).checked).toBe(true);
  });

  it("sends the session cookie and never a token or an Authorization header", async () => {
    const calls = stubProfile();
    const user = userEvent.setup();
    render(<SupplierProfilePage />);
    await screen.findByLabelText("Email notifications");
    await user.click(radio(/no request emails/i));
    await screen.findByText("Saved");

    for (const call of calls.filter((c) => c.url === PREFS_URL)) {
      expect(call.init?.credentials).toBe("include");
      expect(
        JSON.stringify(call.init?.headers ?? {}).toLowerCase(),
      ).not.toContain("authorization");
    }
    expect(calls.every((c) => !c.url.includes("/portal/"))).toBe(true);
  });

  it("reverts the control and says so when the save fails", async () => {
    stubProfile({ preference: "IMMEDIATE", putStatus: 500 });
    const user = userEvent.setup();
    render(<SupplierProfilePage />);
    await screen.findByLabelText("Email notifications");
    await user.click(radio(/no request emails/i));

    await screen.findByRole("alert");
    expect(
      (radio(/as soon as a request arrives/i) as HTMLInputElement).checked,
    ).toBe(true);
    expect((radio(/no request emails/i) as HTMLInputElement).checked).toBe(false);
    expect(screen.queryByText("Saved")).toBeNull();
  });

  it("says the setting is the member's own, not the account's", async () => {
    stubProfile();
    render(<SupplierProfilePage />);
    const section = await screen.findByLabelText("Email notifications");
    expect(section.textContent).toMatch(/your own setting/i);
  });

  it("says plainly that opting out still leaves sign-in emails working", async () => {
    stubProfile();
    render(<SupplierProfilePage />);
    const section = await screen.findByLabelText("Email notifications");
    expect(section.textContent).toMatch(/sign-in links/i);
  });
});

// ---------------------------------------------------------------------------
// The storage sweep (arc 1/3 posture, carried forward)
// ---------------------------------------------------------------------------

describe("notification preferences — no new client-side storage", () => {
  it("writes nothing to localStorage or sessionStorage", async () => {
    stubProfile();
    const user = userEvent.setup();
    render(<SupplierProfilePage />);
    await screen.findByLabelText("Email notifications");
    await user.click(radio(/one summary email a day/i));
    await screen.findByText("Saved");

    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
    expect(storageDump(window.localStorage)).toBe("");
    expect(storageDump(window.sessionStorage)).toBe("");
    expect(document.cookie).toBe("");
  });
});
