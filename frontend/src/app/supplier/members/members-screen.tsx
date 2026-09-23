"use client";

/**
 * MembersScreen — who else from this company can sign in (arc 3 T11, D6).
 *
 * D6, STATED PRECISELY: the UI REFLECTS permissions, the server ENFORCES
 * them. Everything below hides a control the signed-in member's capabilities
 * do not cover, and every one of those controls has a server-side capability
 * gate behind it that returns 403 to anyone who calls the endpoint directly.
 * Hiding is a courtesy that keeps the screen honest about what this person
 * can do; the 403 is the control. A backend test calls a hidden control's
 * endpoint with exactly this screen's credential and gets 403, so the claim
 * is falsifiable rather than asserted.
 *
 * TWO CONTROLS ARE HIDDEN FOR A DIFFERENT REASON: the OWNER row shows no
 * revoke and no role selector, for anyone, including the owner themselves.
 * That is not a permission check — it is the ownership invariant (an account
 * always has exactly one owner, and v1 has no transfer flow). The server
 * refuses those operations for everybody, so offering them would be offering
 * a button that cannot work.
 */

import { useCallback, useEffect, useState } from "react";
import { BRAND_NAME } from "@/lib/brand";
import {
  changeAccountMemberRole,
  getAccountMembers,
  inviteAccountMember,
  revokeAccountMember,
  setAccountMemberRfqContact,
  type AccountMember,
  type SupplierRole,
} from "@/lib/supplier-api";
import type { SupplierSession } from "@/lib/use-supplier-session";
import { GoferLoader } from "@/components/ui/gofer-loader";
import { SupplierShell } from "../supplier-shell";

/** Roles an admin may assign. OWNER is absent by design: ownership is
 *  established, never granted, and the server refuses to grant it. */
const ASSIGNABLE: SupplierRole[] = ["ADMIN", "MEMBER"];

const ROLE_LABEL: Record<SupplierRole, string> = {
  OWNER: "Owner",
  ADMIN: "Admin",
  MEMBER: "Member",
};

/**
 * Server refusals, in this person's own words. There is no enumeration
 * concern here — the caller is an authenticated admin acting inside their own
 * company — so a useful message beats a uniform one.
 */
function errorFor(status: number | undefined): string {
  switch (status) {
    case 403:
      return "You don't have permission to do that.";
    case 409:
      return "That person is already on your team.";
    case 422:
      return "That doesn't look like a valid work email.";
    case 404:
      return "We couldn't find that team member.";
    default:
      return "That didn't go through. Please try again in a moment.";
  }
}

export function MembersScreen({ session }: { session: SupplierSession }) {
  const { can } = session;
  const mayManage = can("manage_members");
  const mayChangeRoles = can("change_roles");

  const [members, setMembers] = useState<AccountMember[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<SupplierRole>("MEMBER");

  const load = useCallback(async () => {
    const res = await getAccountMembers();
    if (res.ok) {
      setMembers(res.data.members);
    } else if (!("unauthorized" in res)) {
      setMembers([]);
      setError(errorFor(res.status));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  /** Run a management call, surface its refusal, refresh on success. */
  const run = async (
    action: () => Promise<{ ok: boolean } & Record<string, unknown>>,
  ) => {
    setBusy(true);
    setError(null);
    const res = await action();
    setBusy(false);
    if (res.ok) {
      await load();
    } else if (!("unauthorized" in res)) {
      setError(errorFor(res.status as number | undefined));
    }
  };

  const shell = (children: React.ReactNode) => (
    <SupplierShell session={session} current="/supplier/members">
      {children}
    </SupplierShell>
  );

  if (members === null) {
    return shell(
      <div className="portal-loading">
        <GoferLoader size={96} aria-label="Loading your team" />
        <p className="portal-loading-text">Loading your team…</p>
      </div>,
    );
  }

  return shell(
    <>
      <div>
        <p className="portal-form-eyebrow">Your team</p>
        <h1 className="portal-form-heading">
          Who can sign in for {session.account?.supplier_domain}
        </h1>
        <p className="portal-form-sub">
          Everyone here can see your buyer requests and quote them. Only the
          people ticked below are emailed when a new one arrives.
          {mayManage
            ? " Admins can also invite and remove colleagues."
            : " Ask an admin on your team to add or remove people."}
        </p>
      </div>

      {error && (
        <div className="portal-soft-error" role="alert" aria-live="polite">
          {error}
        </div>
      )}

      {members.length === 0 ? (
        <section className="supplier-notice" aria-label="No team members">
          <h2 className="supplier-notice-title">No one else yet</h2>
          <div className="supplier-notice-body">
            <p>
              You&apos;re the only person from your company signed up with{" "}
              {BRAND_NAME}.
            </p>
          </div>
        </section>
      ) : (
        <ul className="supplier-member-list" aria-label="Team members">
          {members.map((m) => {
            // The ownership invariant, not a permission: nobody may revoke or
            // re-role the owner, so nobody is shown a control for it.
            const isOwner = m.role === "OWNER";
            return (
              <li key={m.id} className="supplier-member-row">
                <span className="supplier-member-email">{m.email}</span>
                {mayChangeRoles && !isOwner ? (
                  // aria-label carries the name (the row's own email is the
                  // visible context); a visible "Role" caption here would
                  // collide with the invite form's field label.
                  <select
                    className="quote-input"
                    aria-label={`Role for ${m.email}`}
                    value={m.role}
                    disabled={busy}
                    onChange={(e) =>
                      void run(() =>
                        changeAccountMemberRole(
                          m.id,
                          e.target.value as SupplierRole,
                        ),
                      )
                    }
                  >
                    {ASSIGNABLE.map((r) => (
                      <option key={r} value={r}>
                        {ROLE_LABEL[r]}
                      </option>
                    ))}
                  </select>
                ) : (
                  <span className="supplier-member-role">
                    {ROLE_LABEL[m.role] ?? m.role}
                  </span>
                )}
                <span className="supplier-member-status">
                  {m.status === "ACTIVE"
                    ? "Signed up"
                    : m.status === "PENDING"
                      ? "Awaiting approval"
                      : "Removed"}
                </span>
                {/* Arc 4b S1. Separate from the role: everyone here can SEE
                    requests, but only the designated contacts are emailed
                    about them, so one request has one owner rather than five
                    people each assuming another has it. Hidden without
                    manage_members; the server 403s regardless. */}
                {mayManage && m.status !== "REVOKED" && (
                  <label className="supplier-member-rfq">
                    <input
                      type="checkbox"
                      aria-label={`Email ${m.email} about new requests`}
                      checked={m.receives_rfq ?? false}
                      disabled={busy}
                      onChange={(e) =>
                        void run(() =>
                          setAccountMemberRfqContact(m.id, e.target.checked),
                        )
                      }
                    />
                    <span>Email about new requests</span>
                  </label>
                )}
                {mayManage && !isOwner && m.status !== "REVOKED" && (
                  <span className="supplier-member-actions">
                    <button
                      type="button"
                      className="supplier-link-btn"
                      disabled={busy}
                      onClick={() => void run(() => revokeAccountMember(m.id))}
                    >
                      Remove
                    </button>
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {mayManage && (
        <form
          className="quote-form"
          aria-label="Invite a colleague"
          onSubmit={(e) => {
            e.preventDefault();
            const email = inviteEmail.trim();
            if (!email || busy) return;
            void run(async () => {
              const res = await inviteAccountMember(email, inviteRole);
              if (res.ok) setInviteEmail("");
              return res;
            });
          }}
          noValidate
        >
          <p className="portal-form-eyebrow">Invite a colleague</p>
          <div className="quote-field">
            <label className="quote-label" htmlFor="invite-email">
              Work email
            </label>
            <input
              id="invite-email"
              className="quote-input"
              type="email"
              autoComplete="off"
              placeholder="colleague@yourcompany.com"
              value={inviteEmail}
              disabled={busy}
              onChange={(e) => setInviteEmail(e.target.value)}
            />
            <p className="quote-hint">
              A colleague on your company&apos;s email domain is added straight
              away. Anyone else waits for a {BRAND_NAME} representative to
              confirm them.
            </p>
          </div>
          <div className="quote-field">
            <label className="quote-label" htmlFor="invite-role">
              Role
            </label>
            <select
              id="invite-role"
              className="quote-input"
              value={inviteRole}
              disabled={busy}
              onChange={(e) => setInviteRole(e.target.value as SupplierRole)}
            >
              {ASSIGNABLE.map((r) => (
                <option key={r} value={r}>
                  {ROLE_LABEL[r]}
                </option>
              ))}
            </select>
          </div>
          <div className="portal-form-actions">
            <button
              type="submit"
              className="portal-submit"
              disabled={busy || !inviteEmail.trim()}
            >
              Send invitation
            </button>
          </div>
        </form>
      )}
    </>,
  );
}
