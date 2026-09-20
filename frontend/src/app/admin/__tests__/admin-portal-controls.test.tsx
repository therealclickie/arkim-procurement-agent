// @vitest-environment jsdom
/**
 * T7 — admin portal controls (/admin, the token-gated internal surface):
 * the show-once claim link (generate / regenerate / clear-on-tab-switch),
 * the portal-revisions approve/reject endpoints, and the admin-side auth
 * posture (bearer token as a header on every call, never in a URL).
 *
 * T7.4's public-side half — admin auth material never rides along on the
 * PUBLIC routes' fetch calls — is asserted in the portal/quote
 * security.test.tsx files (T3.3); this file pins the admin-side mirror.
 *
 * The admin page reads its token from localStorage("arkim_admin_token") by
 * design (admin surface only); tests seed it to bypass the input gate.
 */
import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AdminInspectorPage from "../page";
import { stubFetch, jsonResponse, type FetchCall } from "@/test-support/fetch-seam";
import { claimLink } from "@/test-support/fixtures";

const ADMIN_TOKEN = "admin_tok_secret_9a8b7c";
const TOKEN_KEY = "arkim_admin_token";
const RAW_TOKEN = "claim_raw_tok_111222333";
const NEW_RAW_TOKEN = "claim_raw_tok_444555666";

const SUPPLIER = { domain: "sealit.example.com", name: "Seal-It Industrial Supply", status: "claimed" };
const REVISION_ROW = {
  id: "rev_item_001",
  kind: "supplier_revision",
  status: "needs_human_review",
  created_at: "2026-09-19T09:00:00Z",
  supplier_domain: "sealit.example.com",
  payload: {
    domain: "sealit.example.com",
    proposed_by: "supplier",
    brands: [{ brand_id: "Goulds", relationship: "AUTHORIZED" }],
    classes: [{ class_id: "SEAL", is_core: true }],
    ship_area: { kind: "NATIONWIDE_US" },
  },
};

/** Route every admin endpoint the touched flows need; 404 for anything else. */
function setupAdmin() {
  window.localStorage.clear();
  window.localStorage.setItem(TOKEN_KEY, ADMIN_TOKEN);
  const calls = stubFetch((call) => {
    const m = call.init?.method ?? "GET";
    if (call.url === "/api/admin/runs" && m === "GET") {
      return jsonResponse(200, { runs: [], count: 0 });
    }
    if (call.url === "/api/admin/suppliers" && m === "GET") {
      return jsonResponse(200, { suppliers: [SUPPLIER], count: 1 });
    }
    if (call.url === "/api/admin/suppliers/claim-link" && m === "POST") {
      return jsonResponse(200, claimLink());
    }
    if (call.url === "/api/admin/suppliers/claim-link/regenerate" && m === "POST") {
      return jsonResponse(
        200,
        claimLink({ token: NEW_RAW_TOKEN, token_id: "tokid_2", link_path: `/portal/${NEW_RAW_TOKEN}` }),
      );
    }
    if (call.url === "/api/admin/review-queue" && m === "GET") {
      return jsonResponse(200, { review_items: [REVISION_ROW] });
    }
    if (/^\/api\/admin\/portal\/revisions\/.+\/(approve|reject)$/.test(call.url) && m === "POST") {
      return jsonResponse(200, { ok: true });
    }
    return jsonResponse(404, { detail: "Not Found" });
  });
  const user = userEvent.setup();
  render(<AdminInspectorPage />);
  return { calls, user };
}

/** Navigate to the suppliers tab and mint the show-once link. */
async function suppliersTabWithClaimLink(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByText("Pipeline Inspector"); // token gate passed via localStorage
  await user.click(screen.getByRole("button", { name: "Suppliers" }));
  await screen.findByRole("button", { name: "Claim link" });
  await user.click(screen.getByRole("button", { name: "Claim link" }));
  await screen.findByText(/Copy and send this now/);
}

function postsTo(calls: FetchCall[], url: string): FetchCall[] {
  return calls.filter((c) => c.url === url && c.init?.method === "POST");
}

function bearer(call: FetchCall): string | undefined {
  const h = call.init?.headers as Record<string, string> | undefined;
  return h?.["Authorization"];
}

describe("T7.1 — show-once claim link (generate)", () => {
  it("displays the raw token ONCE with expiry and the won't-see-again note; posts the domain", async () => {
    const { calls, user } = setupAdmin();
    await suppliersTabWithClaimLink(user);
    // The full URL (origin + link_path) carries the raw token — shown here only.
    const code = document.querySelector("code");
    expect(code?.textContent).toContain(RAW_TOKEN);
    expect(screen.getByText(/won't be visible again/)).toBeTruthy();
    expect(screen.getByText(/expires 2026-09-27/)).toBeTruthy();
    // The mint call posts exactly the supplier domain, bearer-authenticated.
    const mint = postsTo(calls, "/api/admin/suppliers/claim-link");
    expect(mint.length).toBe(1);
    expect(JSON.parse(String(mint[0].init!.body))).toEqual({ supplier_domain: "sealit.example.com" });
    expect(bearer(mint[0])).toBe(`Bearer ${ADMIN_TOKEN}`);
  });

  it("clears the show-once link when switching AWAY from the suppliers tab", async () => {
    const { user } = setupAdmin();
    await suppliersTabWithClaimLink(user);
    await user.click(screen.getByRole("button", { name: "Runs" }));
    await waitFor(() => {
      expect(screen.queryByText(/Copy and send this now/)).toBeNull();
    });
    expect(document.body.textContent ?? "").not.toContain(RAW_TOKEN);
  });

  it("a re-fetch on the SAME tab must not leave the raw token displayed (show-once)", async () => {
    // FINDING (F4, fixed): admin/page.tsx — `load()` (the tab fetch) didn't
    // clear `claimLink`; only the tab-SWITCH effect did. Clicking "Refresh"
    // on the suppliers tab re-fetched the table but left the show-once raw
    // token on screen, breaking the show-once contract the panel itself
    // states ("shown once and won't be visible again"). `load()` now clears
    // the claim link on every fetch.
    const { user } = setupAdmin();
    await suppliersTabWithClaimLink(user);
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => {
      expect(screen.queryByText(/Copy and send this now/)).toBeNull();
    });
    expect(document.body.textContent ?? "").not.toContain(RAW_TOKEN);
  });
});

describe("T7.2 — regenerate replaces the displayed token", () => {
  it("revokes-and-remints: the new raw token displays, the old one is gone", async () => {
    const { calls, user } = setupAdmin();
    await suppliersTabWithClaimLink(user);
    expect(document.body.textContent ?? "").toContain(RAW_TOKEN);
    await user.click(screen.getByRole("button", { name: "Regenerate (revokes this link)" }));
    await waitFor(() => {
      expect(document.body.textContent ?? "").toContain(NEW_RAW_TOKEN);
    });
    expect(document.body.textContent ?? "").not.toContain(RAW_TOKEN);
    // The remint call hits the regenerate endpoint for the same domain.
    const regen = postsTo(calls, "/api/admin/suppliers/claim-link/regenerate");
    expect(regen.length).toBe(1);
    expect(JSON.parse(String(regen[0].init!.body))).toEqual({ supplier_domain: "sealit.example.com" });
  });
});

describe("T7.3 — portal revisions approve / reject", () => {
  it("approve posts to /portal/revisions/{id}/approve and reports applied-to-registry", async () => {
    const { calls, user } = setupAdmin();
    await screen.findByText("Pipeline Inspector");
    await user.click(screen.getByRole("button", { name: "Portal Revisions" }));
    await screen.findByText("Pending supplier-proposed revisions (1)");
    expect(screen.getByText("Goulds · AUTHORIZED")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Approve → apply to registry" }));
    await screen.findByText(/approved → applied to the registry/);
    expect(postsTo(calls, "/api/admin/portal/revisions/rev_item_001/approve").length).toBe(1);
  });

  it("reject posts to /portal/revisions/{id}/reject and reports nothing applied", async () => {
    const { calls, user } = setupAdmin();
    await screen.findByText("Pipeline Inspector");
    await user.click(screen.getByRole("button", { name: "Portal Revisions" }));
    await screen.findByText("Pending supplier-proposed revisions (1)");
    await user.click(screen.getByRole("button", { name: "Reject (discard)" }));
    await screen.findByText(/rejected \(nothing applied\)/);
    expect(postsTo(calls, "/api/admin/portal/revisions/rev_item_001/reject").length).toBe(1);
  });
});

describe("T7.4 — admin-side auth posture", () => {
  it("bearer-authenticates EVERY admin call; the token never appears in any URL", async () => {
    const { calls, user } = setupAdmin();
    await suppliersTabWithClaimLink(user); // GET runs, GET suppliers, POST claim-link
    expect(calls.length).toBeGreaterThanOrEqual(3);
    for (const c of calls) {
      expect(c.url.startsWith("/api/admin/")).toBe(true);
      expect(c.url).not.toContain(ADMIN_TOKEN); // header credential, never a URL query
      expect(bearer(c)).toBe(`Bearer ${ADMIN_TOKEN}`);
    }
  });
});
