// @vitest-environment jsdom
/**
 * Arc 3 T7 — /supplier/profile.
 *
 * Two claims are being pinned:
 *
 *  1. The session payload is IDENTICAL to the token-mode payload. Asserted by
 *     driving the same form through both doors in one test and comparing the
 *     request bodies — not by re-describing the shape, which would pass even
 *     if both had drifted together.
 *  2. Signing in does not turn a proposal into a save. The confirmation says
 *     "submitted for review" and never "saved", because nothing buyer-facing
 *     has changed until a concierge approves.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SupplierProfilePage from "../page";
import { ClaimPage } from "@/app/portal/[token]/claim-page";
import {
  headerValue,
  jsonResponse,
  notFound,
  stubFetch,
  unauthorized,
  type FetchCall,
} from "@/test-support/fetch-seam";
import { portalProfile, supplierMe } from "@/test-support/fixtures";

const FLAG = "NEXT_PUBLIC_SUPPLIER_SESSION_V1";
const TOKEN = "claim_tok_profile";
const replace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/supplier/profile",
}));

beforeEach(() => {
  replace.mockClear();
  vi.stubEnv(FLAG, "1");
});

afterEach(() => {
  vi.unstubAllEnvs();
});

function stubSessionProfile(): FetchCall[] {
  return stubFetch((call) => {
    if (call.url === "/api/supplier/me") return jsonResponse(200, supplierMe());
    if (call.url === "/api/supplier/profile") {
      return jsonResponse(200, portalProfile());
    }
    if (call.url === "/api/supplier/propose-revision") {
      return jsonResponse(200, { ok: true, revision_id: "rev_1", status: "pending" });
    }
    return notFound();
  });
}

// ---------------------------------------------------------------------------
// Payload parity
// ---------------------------------------------------------------------------

describe("session profile — payload parity with token mode", () => {
  it("submits the identical body through both doors", async () => {
    // Session door.
    const sessionCalls = stubSessionProfile();
    const user = userEvent.setup();
    const sessionView = render(<SupplierProfilePage />);
    await screen.findByText("Confirm your profile");
    await user.click(
      screen.getByRole("button", { name: "Submit for review" }),
    );
    await screen.findByText("Submitted for review");
    const sessionBody = JSON.parse(
      String(
        sessionCalls.find(
          (c) => c.url === "/api/supplier/propose-revision",
        )!.init!.body,
      ),
    );
    sessionView.unmount();

    // Token door — the pre-existing claim page, untouched.
    const tokenCalls = stubFetch((call) => {
      if (call.url === `/api/portal/${TOKEN}/profile`) {
        return jsonResponse(200, portalProfile());
      }
      if (call.url === `/api/portal/${TOKEN}/propose-revision`) {
        return jsonResponse(200, { ok: true, revision_id: "rev_1", status: "pending" });
      }
      return notFound();
    });
    const user2 = userEvent.setup();
    render(<ClaimPage token={TOKEN} />);
    await screen.findByText("Confirm your profile");
    await user2.click(screen.getByRole("button", { name: "Submit for review" }));
    await screen.findByText("Submitted for review");
    const tokenBody = JSON.parse(
      String(
        tokenCalls.find(
          (c) => c.url === `/api/portal/${TOKEN}/propose-revision`,
        )!.init!.body,
      ),
    );

    expect(sessionBody).toEqual(tokenBody);
  });

  it("carries an edited brand relationship into the session payload", async () => {
    const calls = stubSessionProfile();
    const user = userEvent.setup();
    render(<SupplierProfilePage />);
    await screen.findByText("Confirm your profile");
    const group = screen.getByRole("radiogroup", {
      name: "Relationship to Goulds",
    });
    await user.click(
      within(group).getByRole("radio", { name: /Authorized distributor/ }),
    );
    await user.click(screen.getByRole("button", { name: "Submit for review" }));
    await screen.findByText("Submitted for review");

    const post = calls.find((c) => c.url === "/api/supplier/propose-revision")!;
    expect(JSON.parse(String(post.init!.body)).brands).toEqual([
      { brand_id: "Goulds", relationship: "AUTHORIZED" },
    ]);
    expect(post.init?.method).toBe("POST");
  });
});

// ---------------------------------------------------------------------------
// Proposal, not save
// ---------------------------------------------------------------------------

describe("session profile — a revision is still a proposal", () => {
  it("confirms submitted-for-review and never says saved", async () => {
    stubSessionProfile();
    const user = userEvent.setup();
    render(<SupplierProfilePage />);
    await screen.findByText("Confirm your profile");
    await user.click(screen.getByRole("button", { name: "Submit for review" }));
    await screen.findByText("Submitted for review");

    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/saved|updated your profile|is now live/i);
    expect(text).toMatch(/pending/i);
    expect(text).toMatch(/until your .* representative approves/i);
  });

  it("keeps the supplier's edits when the submit fails", async () => {
    stubFetch((call) => {
      if (call.url === "/api/supplier/me") return jsonResponse(200, supplierMe());
      if (call.url === "/api/supplier/profile") {
        return jsonResponse(200, portalProfile());
      }
      return jsonResponse(500, { detail: "boom" });
    });
    const user = userEvent.setup();
    render(<SupplierProfilePage />);
    await screen.findByText("Confirm your profile");
    const group = screen.getByRole("radiogroup", {
      name: "Relationship to Goulds",
    });
    await user.click(
      within(group).getByRole("radio", { name: /Authorized distributor/ }),
    );
    await user.click(screen.getByRole("button", { name: "Submit for review" }));

    await screen.findByRole("alert");
    // The form is still there with the edit intact — nothing re-typed.
    const after = screen.getByRole("radiogroup", {
      name: "Relationship to Goulds",
    });
    const checked = within(after).getByRole("radio", {
      name: /Authorized distributor/,
    }) as HTMLInputElement;
    expect(checked.checked).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Network reach and absent profile
// ---------------------------------------------------------------------------

describe("session profile — reach and edge states", () => {
  it("calls ONLY /api/supplier/* and sends no Authorization header", async () => {
    const calls = stubSessionProfile();
    const user = userEvent.setup();
    render(<SupplierProfilePage />);
    await screen.findByText("Confirm your profile");
    await user.click(screen.getByRole("button", { name: "Submit for review" }));
    await screen.findByText("Submitted for review");
    for (const c of calls) {
      expect(c.url.startsWith("/api/supplier/")).toBe(true);
      expect(headerValue(c, "authorization")).toBeNull();
      expect(c.init?.credentials).toBe("include");
    }
  });

  it("says the profile is not ready rather than rendering an empty form", async () => {
    stubFetch((call) =>
      call.url === "/api/supplier/me"
        ? jsonResponse(200, supplierMe())
        : jsonResponse(404, { detail: "No supplier profile for this account" }),
    );
    render(<SupplierProfilePage />);
    expect(await screen.findByText("Your profile isn't ready yet")).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "Submit for review" }),
    ).toBeNull();
  });

  it("redirects to login without a session", async () => {
    stubFetch(() => unauthorized());
    render(<SupplierProfilePage />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/supplier/login"));
  });
});

// ---------------------------------------------------------------------------
// Flag gate
// ---------------------------------------------------------------------------

describe("/supplier/profile route — flag gating", () => {
  it("renders NOTHING and makes no request when the flag is off", () => {
    vi.stubEnv(FLAG, "0");
    const calls = stubSessionProfile();
    const { container } = render(<SupplierProfilePage />);
    expect(container.textContent).toBe("");
    expect(calls).toHaveLength(0);
  });
});
