// @vitest-environment jsdom
/**
 * T2 — ClaimPage (the interactive body of /portal/[token]) characterised
 * across its six states: loading, has-matches, zero-state, uniform rejection,
 * submitted (pending tone), and soft-error (input preserved).
 *
 * All HTTP is mocked at the global-fetch seam (src/test-support/fetch-seam.ts,
 * gate finding I3). The route wrapper page.tsx is covered structurally in
 * portal-route.test.ts (gate finding I2: React 18.3.1 lacks `use`, so the
 * server wrapper is not renderable under vitest).
 */
import { describe, it, expect } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ClaimPage } from "../claim-page";
import { stubFetch, jsonResponse, notFound } from "@/test-support/fetch-seam";
import { portalProfile, teaser } from "@/test-support/fixtures";

const TOKEN = "claim_tok_9f3k2m";
const PROFILE_URL = `/api/portal/${TOKEN}/profile`;
const REVISION_URL = `/api/portal/${TOKEN}/propose-revision`;

/**
 * The standing route set for a live claim page: profile 200, the Night-11
 * quote endpoints 404 (feature off → OpenRequests renders nothing, gate I4),
 * and a configurable propose-revision outcome.
 */
function stubLivePortal(opts: { revision?: () => Response } = {}) {
  return stubFetch((call) => {
    if (call.url === PROFILE_URL && (call.init?.method ?? "GET") === "GET") {
      return jsonResponse(200, portalProfile());
    }
    if (call.url === REVISION_URL && call.init?.method === "POST") {
      return opts.revision ? opts.revision() : jsonResponse(200, {
        ok: true, revision_id: "rev_001", status: "pending",
      });
    }
    return notFound(); // open-requests / quotes → uniform rejected → hidden
  });
}

describe("ClaimPage — state 1: loading", () => {
  it("shows the branded loading indicator while the profile fetch is pending", () => {
    stubFetch(() => new Promise<Response>(() => {})); // never settles
    render(<ClaimPage token={TOKEN} />);
    // First paint is synchronously the loading shell (no flash of rejection).
    expect(screen.getByText("Loading your profile…")).toBeTruthy();
    expect(
      screen.getByRole("img", { name: "Loading your supplier profile" }),
    ).toBeTruthy();
  });
});

describe("ClaimPage — state 2: valid token with real demand", () => {
  it("renders the teaser count and window as the hero", async () => {
    stubLivePortal();
    render(<ClaimPage token={TOKEN} />);
    await screen.findByText("Confirm your profile");
    const hero = document.querySelector(".portal-teaser-hero");
    expect(hero?.textContent).toBe("matched 7 buyer requests in the last 30 days");
  });

  it("places the demand teaser before the profile form in document order", async () => {
    // Demand-as-hero is a product invariant: the teaser must precede the form.
    // (OpenRequests sits between them — gate I4 — so this asserts *before*,
    // not *immediately before*.)
    stubLivePortal();
    render(<ClaimPage token={TOKEN} />);
    await screen.findByText("Confirm your profile");
    const teaserSection = screen.getByText("Buyer demand").closest("section");
    const form = screen.getByText("Tell Gofer what you supply").closest("form");
    expect(teaserSection).toBeTruthy();
    expect(form).toBeTruthy();
    expect(
      (teaserSection!.compareDocumentPosition(form!) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0,
    ).toBe(true);
  });
});

describe("ClaimPage — state 3: zero-state (honesty carve-out)", () => {
  it("renders the framing text and NO count — never a fabricated number", async () => {
    stubFetch((call) => {
      if (call.url === PROFILE_URL) {
        return jsonResponse(
          200,
          portalProfile({
            teaser: teaser({
              has_matches: false,
              count: 0,
              framing:
                "No buyer requests match your profile yet — we will reach out the moment one does.",
            }),
          }),
        );
      }
      return notFound();
    });
    render(<ClaimPage token={TOKEN} />);
    await screen.findByText(
      "No buyer requests match your profile yet — we will reach out the moment one does.",
    );
    const teaserSection = screen.getByText("Buyer demand").closest("section");
    // The absence half of the honesty rule: the zero-state hero carries no
    // digit at all — no "0", no placeholder count, no fabricated number.
    expect(teaserSection?.textContent).not.toMatch(/\d/);
    expect(screen.queryByText(/0 buyer request/)).toBeNull();
  });
});

describe("ClaimPage — state 4: uniform rejection (invalid / expired / reused)", () => {
  it("renders byte-identical output for every non-200 failure shape and network error", async () => {
    // Invalid / expired / reused / flag-off are all non-200 at the client seam
    // (portal-api collapses them). Drive four distinct failures and require
    // the SAME rendered text from each — a true equality check, not four
    // "renders something" checks. No oracle may distinguish the failure kind.
    const failures: Array<{ name: string; respond: () => Response }> = [
      { name: "404 (invalid / flag-off)", respond: () => jsonResponse(404, { detail: "Not Found" }) },
      { name: "410 (expired)", respond: () => jsonResponse(410, { detail: "Gone" }) },
      { name: "500 (reused / server)", respond: () => jsonResponse(500, { detail: "Boom" }) },
      {
        name: "network throw",
        respond: () => {
          throw new Error("network down");
        },
      },
    ];
    const texts: string[] = [];
    for (const f of failures) {
      stubFetch(() => f.respond());
      const { unmount } = render(<ClaimPage token={TOKEN} />);
      await screen.findByText("This link is no longer valid");
      texts.push(document.body.textContent ?? "");
      unmount();
      cleanup();
    }
    expect(texts).toHaveLength(4);
    expect(new Set(texts).size).toBe(1);
  });
});

describe("ClaimPage — state 5: submitted (pending tone)", () => {
  it("confirms with 'Submitted for review' wording — never 'saved'", async () => {
    stubLivePortal();
    const user = userEvent.setup();
    render(<ClaimPage token={TOKEN} />);
    await screen.findByText("Confirm your profile");
    await user.click(screen.getByRole("button", { name: "Submit for review" }));
    await screen.findByText("Submitted for review");
    // Pending tone: the clock glyph is labelled "Pending", and the word
    // "saved" appears nowhere — nothing is live until the concierge approves.
    expect(screen.getByRole("img", { name: "Pending" })).toBeTruthy();
    expect(document.body.textContent ?? "").not.toMatch(/saved/i);
  });
});

describe("ClaimPage — state 6: submit error (input preserved)", () => {
  it("keeps the supplier's entered input in the DOM after a failed submit", async () => {
    stubLivePortal({ revision: () => jsonResponse(500, { detail: "Boom" }) });
    const user = userEvent.setup();
    render(<ClaimPage token={TOKEN} />);
    await screen.findByText("Confirm your profile");

    // The supplier adds a brand (the edit this test must see preserved)…
    await user.type(screen.getByLabelText("Brand name to add"), "Grundfos");
    await user.click(screen.getByRole("button", { name: "+ Add brand" }));
    expect(await screen.findByText("Grundfos")).toBeTruthy();

    // …then submits and the revision POST fails.
    await user.click(screen.getByRole("button", { name: "Submit for review" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/couldn't submit your changes/i);
    expect(alert.textContent).toMatch(/kept/i);
    // The edit is still present — the supplier never re-enters anything.
    expect(screen.getByText("Grundfos")).toBeTruthy();
  });
});
