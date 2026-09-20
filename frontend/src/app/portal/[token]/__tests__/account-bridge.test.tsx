// @vitest-environment jsdom
/**
 * Arc 3 T9 — the claim → account bridge (D4).
 *
 * The claim flow used to end at "submitted for review". It now offers an
 * account. The tests that matter are the ones about what it must NOT do:
 * consume the claim token, imply the supplier is signed in, distinguish a
 * known address from an unknown one, or disturb the pre-existing claim flow
 * in any way when the flag is off.
 *
 * NOTE: this is a NEW file in the same directory as the pre-existing
 * characterisation tests. None of those are modified — with the flag off (the
 * default) the bridge renders nothing at all, which is why they still pass.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ClaimPage } from "../claim-page";
import {
  jsonResponse,
  notFound,
  stubFetch,
  tooManyRequests,
  type FetchCall,
} from "@/test-support/fetch-seam";
import { portalProfile } from "@/test-support/fixtures";

const FLAG = "NEXT_PUBLIC_SUPPLIER_SESSION_V1";
const TOKEN = "claim_tok_bridge_1";
const PROFILE_URL = `/api/portal/${TOKEN}/profile`;
const REVISION_URL = `/api/portal/${TOKEN}/propose-revision`;
const BRIDGE_URL = `/api/portal/${TOKEN}/request-account`;

beforeEach(() => {
  vi.stubEnv(FLAG, "1");
});

afterEach(() => {
  vi.unstubAllEnvs();
});

function stubClaim(bridge: () => Response = () => jsonResponse(200, { ok: true })) {
  return stubFetch((call) => {
    if (call.url === PROFILE_URL) return jsonResponse(200, portalProfile());
    if (call.url === REVISION_URL) {
      return jsonResponse(200, { ok: true, revision_id: "rev_1", status: "pending" });
    }
    if (call.url === BRIDGE_URL) return bridge();
    return notFound();
  });
}

/** Claim page → submit → the submitted state. */
async function submitTheClaim() {
  const user = userEvent.setup();
  render(<ClaimPage token={TOKEN} />);
  await screen.findByText("Confirm your profile");
  await user.click(screen.getByRole("button", { name: "Submit for review" }));
  await screen.findByText("Submitted for review");
  return user;
}

// ---------------------------------------------------------------------------
// The CTA
// ---------------------------------------------------------------------------

describe("claim → account bridge — the offer", () => {
  it("appears after a successful submit, not before", async () => {
    stubClaim();
    const user = userEvent.setup();
    render(<ClaimPage token={TOKEN} />);
    await screen.findByText("Confirm your profile");
    expect(screen.queryByTestId("account-bridge")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Submit for review" }));
    await screen.findByText("Submitted for review");
    expect(screen.getByRole("button", { name: "Create your account" })).toBeTruthy();
  });

  it("posts to the bridge endpoint with the supplied email", async () => {
    const calls = stubClaim();
    const user = await submitTheClaim();
    await user.click(screen.getByRole("button", { name: "Create your account" }));
    await user.type(screen.getByLabelText("Work email"), "sales@dxpe.com");
    await user.click(
      screen.getByRole("button", { name: "Email me a sign-in link" }),
    );
    await screen.findByText("Check your email");

    const post = calls.find((c: FetchCall) => c.url === BRIDGE_URL);
    expect(post).toBeTruthy();
    expect(post!.init?.method).toBe("POST");
    expect(JSON.parse(String(post!.init!.body))).toEqual({
      email: "sales@dxpe.com",
    });
  });
});

// ---------------------------------------------------------------------------
// What it must not do
// ---------------------------------------------------------------------------

describe("claim → account bridge — honesty and non-consumption", () => {
  it("does not consume the claim token — no second revision call, no re-fetch", async () => {
    const calls = stubClaim();
    const user = await submitTheClaim();
    const before = calls.length;
    await user.click(screen.getByRole("button", { name: "Create your account" }));
    await user.type(screen.getByLabelText("Work email"), "sales@dxpe.com");
    await user.click(
      screen.getByRole("button", { name: "Email me a sign-in link" }),
    );
    await screen.findByText("Check your email");

    // Exactly ONE extra request was made, and it was the bridge.
    expect(calls.length).toBe(before + 1);
    expect(calls[calls.length - 1].url).toBe(BRIDGE_URL);
    // The claim flow was not re-driven or invalidated.
    expect(calls.filter((c) => c.url === REVISION_URL)).toHaveLength(1);
  });

  it("never says the supplier is signed in", async () => {
    stubClaim();
    const user = await submitTheClaim();
    await user.click(screen.getByRole("button", { name: "Create your account" }));
    await user.type(screen.getByLabelText("Work email"), "sales@dxpe.com");
    await user.click(
      screen.getByRole("button", { name: "Email me a sign-in link" }),
    );
    await screen.findByText("Check your email");
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/you('re| are) (now )?(signed|logged) in/i);
    expect(text).not.toMatch(/account created|welcome back/i);
    expect(text).toMatch(/we've sent a sign-in link/i);
  });

  it("never uses the word 'saved' anywhere on the submitted state", async () => {
    // The pre-existing characterisation test asserts this for the card; the
    // bridge is inside that card, so the constraint extends to it.
    stubClaim();
    const user = await submitTheClaim();
    expect(document.body.textContent ?? "").not.toMatch(/saved/i);
    await user.click(screen.getByRole("button", { name: "Create your account" }));
    expect(document.body.textContent ?? "").not.toMatch(/saved/i);
    await user.type(screen.getByLabelText("Work email"), "sales@dxpe.com");
    await user.click(
      screen.getByRole("button", { name: "Email me a sign-in link" }),
    );
    await screen.findByText("Check your email");
    expect(document.body.textContent ?? "").not.toMatch(/saved/i);
  });

  it("keeps the pending glyph — the revision is still not live", async () => {
    stubClaim();
    await submitTheClaim();
    expect(screen.getByRole("img", { name: "Pending" })).toBeTruthy();
  });

  it("renders one confirmation regardless of the outcome (D5)", async () => {
    // The endpoint answers 200 {"ok":true} for established / already-existing
    // / pending-member / unparseable alike. The UI must not add a branch.
    const read = async (email: string) => {
      cleanup();          // one claim page on screen at a time
      stubClaim();
      const user = await submitTheClaim();
      await user.click(
        screen.getByRole("button", { name: "Create your account" }),
      );
      await user.type(screen.getByLabelText("Work email"), email);
      await user.click(
        screen.getByRole("button", { name: "Email me a sign-in link" }),
      );
      await screen.findByText("Check your email");
      const text = screen.getByTestId("account-bridge").textContent ?? "";
      return text;
    };
    const outputs = new Set([
      await read("known@dxpe.com"),
      await read("stranger@elsewhere.example"),
      await read("garbage"),
    ]);
    expect(outputs.size).toBe(1);
  });

  it("shows a neutral retry on failure, revealing nothing about the address", async () => {
    stubClaim(() => tooManyRequests());
    const user = await submitTheClaim();
    await user.click(screen.getByRole("button", { name: "Create your account" }));
    await user.type(screen.getByLabelText("Work email"), "sales@dxpe.com");
    await user.click(
      screen.getByRole("button", { name: "Email me a sign-in link" }),
    );
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/try again in a moment/i);
    expect(alert.textContent).not.toMatch(/account|address|exists|not found/i);
  });
});

// ---------------------------------------------------------------------------
// Flag off — the claim page is exactly what it was
// ---------------------------------------------------------------------------

describe("claim → account bridge — flag off", () => {
  it("renders no bridge and makes no extra request", async () => {
    vi.stubEnv(FLAG, "0");
    const calls = stubClaim();
    await submitTheClaim();
    expect(screen.queryByTestId("account-bridge")).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Create your account" }),
    ).toBeNull();
    expect(calls.some((c) => c.url === BRIDGE_URL)).toBe(false);
  });

  it("leaves the submitted card's content unchanged", async () => {
    vi.stubEnv(FLAG, "0");
    stubClaim();
    await submitTheClaim();
    expect(screen.getByText("Submitted for review")).toBeTruthy();
    expect(screen.getByRole("img", { name: "Pending" })).toBeTruthy();
    expect(document.body.textContent ?? "").not.toMatch(/saved/i);
  });
});
