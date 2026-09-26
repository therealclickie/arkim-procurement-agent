"use client";

/**
 * Approval policy (arc 6, D5) — the company's auto-approval limit and whether
 * an Admin may override it. STORED and AUDITED now; the order flow starts
 * enforcing it in a later release, and this screen says so rather than
 * implying a control that does not yet act.
 *
 * Shown only to a member holding `set_approval_limit`; the override switch
 * additionally needs `toggle_admin_override`. The server re-checks both.
 */

import { useEffect, useState } from "react";
import { getBuyerSettings, saveApprovalPolicy, type ApprovalPolicy } from "@/lib/buyer-api";
import { useBuyerSession } from "@/lib/buyer-session";

const NO_ACCESS = "Only your company's admins can change the approval policy.";

export function ApprovalPolicyScreen() {
  const session = useBuyerSession();
  const allowed = Boolean(session?.can("set_approval_limit"));
  const canToggle = Boolean(session?.can("toggle_admin_override"));
  const [policy, setPolicy] = useState<ApprovalPolicy | null>(null);
  const [limit, setLimit] = useState("");
  const [override, setOverride] = useState(true);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!allowed) return;
    void getBuyerSettings().then((r) => {
      if (r.ok) {
        setPolicy(r.data.approval_policy);
        setLimit(String(r.data.approval_policy.auto_approval_limit));
        setOverride(r.data.approval_policy.allow_admin_override);
      }
    });
  }, [allowed]);

  if (!session) return null;
  if (!allowed) return <div className="proc-page"><p className="rc-note">{NO_ACCESS}</p></div>;

  const onSave = async (event: React.FormEvent) => {
    event.preventDefault();
    const value = Number(limit);
    if (limit.trim() === "" || !Number.isFinite(value) || value < 0) {
      setMessage("Enter a dollar amount of 0 or more.");
      return;
    }
    setBusy(true);
    const change: { auto_approval_limit: number; allow_admin_override?: boolean } =
      { auto_approval_limit: value };
    if (canToggle) change.allow_admin_override = override;
    const r = await saveApprovalPolicy(change);
    setBusy(false);
    if (r.ok) {
      setPolicy(r.data.approval_policy);
      setMessage("Saved.");
    } else {
      setMessage("rejected" in r && r.status === 422
        ? "Enter a dollar amount of 0 or more."
        : "rejected" in r && r.status === 403 ? NO_ACCESS : "Couldn't save — please try again.");
    }
  };

  return (
    <form onSubmit={onSave} className="proc-page"
          style={{ display: "flex", flexDirection: "column", gap: 16, padding: 24, maxWidth: 560 }}>
      <div>
        <h1 style={{ fontSize: 20, fontWeight: 600 }}>Approval policy</h1>
        <p className="rc-note">
          Orders above the limit will need a second approval. Setting it to $0 means every
          order does. This policy is recorded now; ordering starts enforcing it in an
          upcoming release.
        </p>
      </div>

      <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 13 }}>
        Auto-approval limit (USD)
        <input
          aria-label="Auto-approval limit"
          inputMode="decimal"
          value={limit}
          onChange={(e) => setLimit(e.target.value)}
          disabled={busy || !policy}
        />
      </label>

      <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}>
        <input
          type="checkbox"
          aria-label="Allow admin override"
          checked={override}
          onChange={(e) => setOverride(e.target.checked)}
          disabled={busy || !policy || !canToggle}
        />
        Allow an admin to approve alone above the limit
      </label>

      {message && <p role="status" className="rc-note">{message}</p>}

      <div>
        <button type="submit" className="proc-btn" data-kind="primary" disabled={busy || !policy}>
          {busy ? "Saving…" : "Save policy"}
        </button>
      </div>
    </form>
  );
}
