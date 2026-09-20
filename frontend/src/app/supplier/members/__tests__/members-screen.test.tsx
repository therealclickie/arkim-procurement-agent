// @vitest-environment jsdom
/**
 * Arc 3 T11 — /supplier/members (D6).
 *
 * The claim under test is NOT "a MEMBER cannot invite" — the UI could never
 * establish that. It is "the UI reflects what the server enforces": controls
 * a member's capabilities do not cover are absent, AND the endpoint behind a
 * hidden control refuses that member directly. The second half lives in
 * `utils/procurement_agent/tests/test_supplier_members_rbac_cookie.py`, which
 * drives the real routes with the real cookie session and gets 403.
 *
 * Without that pairing, hiding a button would be indistinguishable from
 * enforcing a policy, which is exactly the confusion D6 exists to prevent.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SupplierMembersPage from "../page";
import {
  jsonResponse,
  notFound,
  stubFetch,
  unauthorized,
  type FetchCall,
} from "@/test-support/fetch-seam";
import {
  accountMember,
  supplierMe,
  supplierMember,
} from "@/test-support/fixtures";

const FLAG = "NEXT_PUBLIC_SUPPLIER_SESSION_V1";
const replace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/supplier/members",
}));

beforeEach(() => {
  replace.mockClear();
  vi.stubEnv(FLAG, "1");
});

afterEach(() => {
  vi.unstubAllEnvs();
});

const OWNER_ROW = accountMember({
  id: "mem_owner", email: "owner@sealit.example.com", role: "OWNER",
});
const MEMBER_ROW = accountMember({
  id: "mem_bob", email: "bob@sealit.example.com", role: "MEMBER",
});

/**
 * A signed-in supplier at `role`, with an owner and one ordinary member on
 * the team. `management` decides what the invite/role/revoke calls return.
 */
function stubTeam(
  role: "OWNER" | "ADMIN" | "MEMBER",
  management: () => Response = () => jsonResponse(200, { ok: true, member: MEMBER_ROW }),
): FetchCall[] {
  return stubFetch((call) => {
    if (call.url === "/api/supplier/me") {
      return jsonResponse(200, supplierMe({ member: supplierMember({ role }) }));
    }
    if (call.url === "/api/supplier/members" &&
        (call.init?.method ?? "GET") === "GET") {
      return jsonResponse(200, { members: [OWNER_ROW, MEMBER_ROW] });
    }
    if (call.url.startsWith("/api/supplier/members")) return management();
    return notFound();
  });
}

// ---------------------------------------------------------------------------
// Reflection of capabilities
// ---------------------------------------------------------------------------

describe("members — a MEMBER sees no management controls", () => {
  it("lists colleagues but offers no invite, role or remove control", async () => {
    stubTeam("MEMBER");
    render(<SupplierMembersPage />);
    await screen.findByText("bob@sealit.example.com");
    // They CAN see who is on the team — view_members is in every role.
    expect(screen.getByText("owner@sealit.example.com")).toBeTruthy();
    // ...and nothing that manages them.
    expect(screen.queryByRole("form", { name: "Invite a colleague" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Send invitation" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
    expect(
      screen.queryByRole("combobox", { name: /Role for/ }),
    ).toBeNull();
  });

  it("tells them who to ask instead of showing a dead control", async () => {
    stubTeam("MEMBER");
    render(<SupplierMembersPage />);
    await screen.findByText("bob@sealit.example.com");
    expect(document.body.textContent).toMatch(/ask an admin on your team/i);
  });
});

describe("members — an ADMIN sees the management controls", () => {
  it("offers invite, role and remove", async () => {
    stubTeam("ADMIN");
    render(<SupplierMembersPage />);
    await screen.findByText("bob@sealit.example.com");
    expect(screen.getByRole("button", { name: "Send invitation" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Remove" })).toBeTruthy();
    expect(
      screen.getByRole("combobox", {
        name: "Role for bob@sealit.example.com",
      }),
    ).toBeTruthy();
  });

  it("never offers OWNER as an assignable role", async () => {
    // Ownership is established, never granted; the server refuses to grant it.
    stubTeam("ADMIN");
    render(<SupplierMembersPage />);
    await screen.findByText("bob@sealit.example.com");
    const select = screen.getByRole("combobox", {
      name: "Role for bob@sealit.example.com",
    }) as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.value).sort()).toEqual([
      "ADMIN", "MEMBER",
    ]);
  });

  it("offers no remove or role control on the OWNER row", async () => {
    // The ownership invariant, not a permission: the server refuses these for
    // EVERYONE, so offering them would be offering a button that cannot work.
    stubTeam("OWNER");
    render(<SupplierMembersPage />);
    await screen.findByText("owner@sealit.example.com");
    expect(
      screen.queryByRole("combobox", {
        name: "Role for owner@sealit.example.com",
      }),
    ).toBeNull();
    // Exactly one Remove button — the ordinary member's.
    expect(screen.getAllByRole("button", { name: "Remove" })).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// The management calls
// ---------------------------------------------------------------------------

describe("members — management actions", () => {
  it("invites with the chosen email and role, then reloads the list", async () => {
    const calls = stubTeam("ADMIN");
    const user = userEvent.setup();
    render(<SupplierMembersPage />);
    await screen.findByText("bob@sealit.example.com");
    const before = calls.filter((c) => c.url === "/api/supplier/members").length;

    await user.type(screen.getByLabelText("Work email"), "new@sealit.example.com");
    await user.selectOptions(screen.getByLabelText("Role"), "ADMIN");
    await user.click(screen.getByRole("button", { name: "Send invitation" }));

    await waitFor(() =>
      expect(
        calls.some((c) => c.url === "/api/supplier/members/invite"),
      ).toBe(true),
    );
    const invite = calls.find((c) => c.url === "/api/supplier/members/invite")!;
    expect(invite.init?.method).toBe("POST");
    expect(JSON.parse(String(invite.init!.body))).toEqual({
      email: "new@sealit.example.com",
      role: "ADMIN",
    });
    // The list is re-read rather than optimistically patched — the server is
    // the source of truth for who is on the team and at what status.
    await waitFor(() =>
      expect(
        calls.filter((c) => c.url === "/api/supplier/members").length,
      ).toBeGreaterThan(before),
    );
  });

  it("changes a role through the role endpoint", async () => {
    const calls = stubTeam("ADMIN");
    const user = userEvent.setup();
    render(<SupplierMembersPage />);
    await screen.findByText("bob@sealit.example.com");
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Role for bob@sealit.example.com" }),
      "ADMIN",
    );
    await waitFor(() =>
      expect(
        calls.some((c) => c.url === "/api/supplier/members/mem_bob/role"),
      ).toBe(true),
    );
    const call = calls.find(
      (c) => c.url === "/api/supplier/members/mem_bob/role",
    )!;
    expect(JSON.parse(String(call.init!.body))).toEqual({ role: "ADMIN" });
  });

  it("revokes through the revoke endpoint", async () => {
    const calls = stubTeam("ADMIN");
    const user = userEvent.setup();
    render(<SupplierMembersPage />);
    await screen.findByText("bob@sealit.example.com");
    await user.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() =>
      expect(
        calls.some((c) => c.url === "/api/supplier/members/mem_bob/revoke"),
      ).toBe(true),
    );
  });

  it("calls ONLY /api/supplier/* with the cookie and no Authorization", async () => {
    const calls = stubTeam("ADMIN");
    const user = userEvent.setup();
    render(<SupplierMembersPage />);
    await screen.findByText("bob@sealit.example.com");
    await user.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(calls.length).toBeGreaterThan(2));
    for (const c of calls) {
      expect(c.url.startsWith("/api/supplier/")).toBe(true);
      expect(new Headers(c.init?.headers).get("authorization")).toBeNull();
      expect(c.init?.credentials).toBe("include");
    }
  });
});

// ---------------------------------------------------------------------------
// Server refusals — the UI reports them, it does not pre-empt them
// ---------------------------------------------------------------------------

describe("members — server refusals are surfaced honestly", () => {
  it("shows the 403 when the server refuses an action the UI offered", async () => {
    // This is the important one: the UI's idea of permissions can be stale
    // (a role change elsewhere, a revoked membership). When it is, the server
    // wins and the supplier is told — not left with a control that silently
    // does nothing.
    stubTeam("ADMIN", () => jsonResponse(403, { detail: "Forbidden" }));
    const user = userEvent.setup();
    render(<SupplierMembersPage />);
    await screen.findByText("bob@sealit.example.com");
    await user.click(screen.getByRole("button", { name: "Remove" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/don't have permission/i);
  });

  it("reports an already-a-member conflict in the admin's own words", async () => {
    stubTeam("ADMIN", () => jsonResponse(409, { detail: "already_member" }));
    const user = userEvent.setup();
    render(<SupplierMembersPage />);
    await screen.findByText("bob@sealit.example.com");
    await user.type(screen.getByLabelText("Work email"), "bob@sealit.example.com");
    await user.click(screen.getByRole("button", { name: "Send invitation" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/already on your team/i);
  });

  it("reports an invalid email as 422 rather than pre-validating it away", async () => {
    stubTeam("ADMIN", () => jsonResponse(422, { detail: "invalid_email" }));
    const user = userEvent.setup();
    render(<SupplierMembersPage />);
    await screen.findByText("bob@sealit.example.com");
    await user.type(screen.getByLabelText("Work email"), "nonsense");
    await user.click(screen.getByRole("button", { name: "Send invitation" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/valid work email/i);
  });
});

// ---------------------------------------------------------------------------
// Guard + flag
// ---------------------------------------------------------------------------

describe("/supplier/members route", () => {
  it("redirects to login without a session", async () => {
    stubFetch(() => unauthorized());
    render(<SupplierMembersPage />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/supplier/login"));
  });

  it("renders NOTHING and makes no request when the flag is off", () => {
    vi.stubEnv(FLAG, "0");
    const calls = stubTeam("ADMIN");
    const { container } = render(<SupplierMembersPage />);
    expect(container.textContent).toBe("");
    expect(calls).toHaveLength(0);
  });
});
