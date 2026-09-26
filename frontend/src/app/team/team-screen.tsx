"use client";

/**
 * Team (arc 6, D3) — a company Admin's member management: invite (default
 * Requester), change role, revoke. Membership is by invitation only.
 *
 * Shown only to a member holding `manage_members`; every action is re-checked
 * by the server, which also owns the rules this screen merely reports (the
 * last Admin cannot be demoted or revoked; one company per person).
 */

import { useCallback, useEffect, useState } from "react";
import {
  BUYER_ROLES,
  changeBuyerMemberRole,
  inviteBuyerMember,
  listBuyerMembers,
  revokeBuyerMember,
  roleLabel,
  type BuyerMember,
  type BuyerRole,
} from "@/lib/buyer-api";
import { useBuyerSession } from "@/lib/buyer-session";

const NO_ACCESS = "Only your company's admins can manage the team.";

function problem(status?: number, detail?: string): string {
  if (status === 409 && detail) return detail;
  if (status === 422) return "That doesn't look like a usable email address.";
  if (status === 403) return NO_ACCESS;
  return "Something went wrong — please try again.";
}

export function TeamScreen() {
  const session = useBuyerSession();
  const allowed = Boolean(session?.can("manage_members"));
  const [members, setMembers] = useState<BuyerMember[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<BuyerRole>("REQUESTER");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    const r = await listBuyerMembers();
    if (r.ok) setMembers(r.data.members);
    setLoaded(true);
  }, []);

  useEffect(() => {
    if (allowed) void load();
  }, [allowed, load]);

  if (!session) return null;
  if (!allowed) return <div className="proc-page"><p className="rc-note">{NO_ACCESS}</p></div>;

  const onInvite = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!email.trim() || busy) return;
    setBusy(true);
    const r = await inviteBuyerMember(email.trim(), role);
    setBusy(false);
    if (r.ok) {
      setMessage(`Invitation sent to ${r.data.member.email}.`);
      setEmail("");
      setRole("REQUESTER");
      await load();
    } else {
      setMessage("rejected" in r ? problem(r.status, r.detail) : NO_ACCESS);
    }
  };

  const onRole = async (m: BuyerMember, next: BuyerRole) => {
    const r = await changeBuyerMemberRole(m.id, next);
    setMessage(r.ok ? `${m.email} is now ${roleLabel(next)}.`
      : ("rejected" in r ? problem(r.status, r.detail) : NO_ACCESS));
    await load();
  };

  const onRevoke = async (m: BuyerMember) => {
    const r = await revokeBuyerMember(m.id);
    setMessage(r.ok ? `${m.email} no longer has access.`
      : ("rejected" in r ? problem(r.status, r.detail) : NO_ACCESS));
    await load();
  };

  return (
    <div className="proc-page" style={{ display: "flex", flexDirection: "column", gap: 18, padding: 24 }}>
      <div>
        <h1 style={{ fontSize: 20, fontWeight: 600 }}>Team</h1>
        <p className="rc-note">
          People at {session.company.name} who can use this account. New members start as
          Requesters unless you choose otherwise.
        </p>
      </div>

      <form onSubmit={onInvite} style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }}>
          Email
          <input
            aria-label="Invite email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="colleague@company.com"
            disabled={busy}
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }}>
          Role
          <select aria-label="Invite role" value={role} onChange={(e) => setRole(e.target.value as BuyerRole)}>
            {BUYER_ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
          </select>
        </label>
        <button type="submit" className="proc-btn" data-kind="primary" disabled={busy || !email.trim()}>
          {busy ? "Inviting…" : "Invite"}
        </button>
      </form>

      {message && <p role="status" className="rc-note">{message}</p>}

      {loaded && (
        <table style={{ width: "100%", fontSize: 13 }}>
          <thead>
            <tr><th align="left">Email</th><th align="left">Role</th><th align="left">Status</th><th /></tr>
          </thead>
          <tbody>
            {members.map((m) => (
              <tr key={m.id}>
                <td>{m.email}</td>
                <td>
                  {m.status === "ACTIVE" ? (
                    <select
                      aria-label={`Role for ${m.email}`}
                      value={m.role}
                      onChange={(e) => void onRole(m, e.target.value as BuyerRole)}
                    >
                      {BUYER_ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
                    </select>
                  ) : roleLabel(m.role)}
                </td>
                <td>{m.status === "ACTIVE" ? "Active" : "Revoked"}</td>
                <td>
                  {m.status === "ACTIVE" && (
                    <button className="proc-btn" data-kind="quiet" onClick={() => void onRevoke(m)}>
                      Revoke
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
