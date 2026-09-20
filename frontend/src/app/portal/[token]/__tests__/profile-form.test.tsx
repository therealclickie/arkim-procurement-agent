// @vitest-environment jsdom
/**
 * T4 — ProfileForm characterised through the real ClaimPage submit path:
 * the tri-state brand relationship, both ship-area payload shapes, the
 * conditional aftermarket disclosure, and the fresh-claim empty state.
 * Payload assertions read the actual propose-revision POST body at the
 * fetch seam — not a unit call into the mapper.
 */
import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ClaimPage } from "../claim-page";
import { stubFetch, jsonResponse, notFound, isPostTo } from "@/test-support/fetch-seam";
import { portalProfile } from "@/test-support/fixtures";
import type { ProposeRevisionBody } from "@/lib/portal-api";

const TOKEN = "claim_tok_9f3k2m";
const PROFILE_URL = `/api/portal/${TOKEN}/profile`;
const REVISION_URL = `/api/portal/${TOKEN}/propose-revision`;

function setup(profile = portalProfile()) {
  const calls = stubFetch((call) => {
    if (call.url === PROFILE_URL && (call.init?.method ?? "GET") === "GET") {
      return jsonResponse(200, profile);
    }
    if (isPostTo(call, REVISION_URL)) {
      return jsonResponse(200, { ok: true, revision_id: "rev_001", status: "pending" });
    }
    return notFound();
  });
  const user = userEvent.setup();
  render(<ClaimPage token={TOKEN} />);
  return { calls, user };
}

/** The propose-revision body of the (single) submit this test performed. */
async function submitAndReadBody(
  calls: ReturnType<typeof stubFetch>,
  user: ReturnType<typeof userEvent.setup>,
): Promise<ProposeRevisionBody> {
  await user.click(screen.getByRole("button", { name: "Submit for review" }));
  await screen.findByText("Submitted for review");
  const post = calls.find((c) => isPostTo(c, REVISION_URL));
  expect(post).toBeTruthy();
  return JSON.parse(String(post!.init!.body)) as ProposeRevisionBody;
}

describe("T4.1 — tri-state brand relationship", () => {
  it("offers exactly AUTHORIZED | CARRIES | AFTERMARKET_COMPATIBLE, no more, no less", async () => {
    setup();
    await screen.findByText("Confirm your profile");
    const group = screen.getByRole("radiogroup", { name: "Relationship to Goulds" });
    const values = Array.from(group.querySelectorAll("input[type=radio]"))
      .map((el) => (el as HTMLInputElement).value)
      .sort();
    expect(values).toEqual(["AFTERMARKET_COMPATIBLE", "AUTHORIZED", "CARRIES"]);
    // The human-readable labels are present for all three. (Scoped to this
    // group: the add-brand control renders the same three options again.)
    const labels = Array.from(group.querySelectorAll("span"))
      .map((el) => el.textContent ?? "");
    for (const label of ["Authorized distributor", "Carries / stocks", "Aftermarket-compatible"]) {
      expect(labels).toContain(label);
    }
  });

  it("carries the chosen relationship into the submit payload", async () => {
    const { calls, user } = setup();
    await screen.findByText("Confirm your profile");
    // Default from the profile is CARRIES; the supplier upgrades to AUTHORIZED.
    // Scoped to the Goulds row — the add-brand control also has an
    // "Authorized distributor" radio (a second, distinct control).
    const group = screen.getByRole("radiogroup", { name: "Relationship to Goulds" });
    await user.click(within(group).getByRole("radio", { name: /Authorized distributor/ }));
    const body = await submitAndReadBody(calls, user);
    expect(body.brands).toEqual([
      { brand_id: "Goulds", relationship: "AUTHORIZED" },
    ]);
  });
});

describe("T4.2 — ship-area payload shapes", () => {
  it("submits {kind:'NATIONWIDE_US'} when nationwide is selected", async () => {
    const { calls, user } = setup();
    await screen.findByText("Confirm your profile");
    const body = await submitAndReadBody(calls, user);
    expect(body.ship_area).toEqual({ kind: "NATIONWIDE_US" });
  });

  it("submits {kind:'STATES', states:[...]} with the selected states, sorted", async () => {
    const { calls, user } = setup();
    await screen.findByText("Confirm your profile");
    await user.click(screen.getByRole("radio", { name: /Specific states/ }));
    // The states grid appears only after selecting Specific states.
    await user.click(screen.getByRole("checkbox", { name: "TX" }));
    await user.click(screen.getByRole("checkbox", { name: "CA" }));
    const body = await submitAndReadBody(calls, user);
    expect(body.ship_area).toEqual({ kind: "STATES", states: ["CA", "TX"] });
  });
});

describe("T4.3 — aftermarket disclosure", () => {
  it("renders when aftermarket_disclosure is non-null", async () => {
    setup(portalProfile({ aftermarket_disclosure: "Buyers see an aftermarket note on your quotes." }));
    await screen.findByText("Confirm your profile");
    const note = screen.getByRole("note");
    expect(note.textContent).toContain("Buyers see an aftermarket note on your quotes.");
    expect(screen.getByText("Aftermarket parts notice")).toBeTruthy();
  });

  it("is absent when aftermarket_disclosure is null", async () => {
    setup(portalProfile({ aftermarket_disclosure: null }));
    await screen.findByText("Confirm your profile");
    expect(screen.queryByRole("note")).toBeNull();
    expect(screen.queryByText("Aftermarket parts notice")).toBeNull();
  });
});

describe("T4.4 — freshly-claimed supplier (empty profile)", () => {
  it("renders the normal fill-me-in state, not an error state", async () => {
    setup(
      portalProfile({
        brands: [],
        classes: [],
        ship_area: null,
        name: null,
      }),
    );
    await screen.findByText("Confirm your profile");
    // Honest empty copy for both empty sections…
    expect(
      screen.getByText("No brands listed yet — add the brands you carry below."),
    ).toBeTruthy();
    expect(
      screen.getByText(/No classes listed yet\./),
    ).toBeTruthy();
    // …a working default ship-area (null → nationwide), not a blank/broken control…
    expect(screen.getByRole("radio", { name: /Nationwide US/ })).toBeTruthy();
    // …and NO error semantics anywhere (no alert, no error role, no failure copy).
    expect(screen.queryByRole("alert")).toBeNull();
    expect(document.body.textContent ?? "").not.toMatch(/error|failed|invalid/i);
    // The single submit action is present and enabled.
    const submit = screen.getByRole("button", { name: "Submit for review" });
    expect((submit as HTMLButtonElement).disabled).toBe(false);
  });
});
