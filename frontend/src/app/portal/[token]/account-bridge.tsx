"use client";

/**
 * AccountBridge — "create your account", offered after a successful claim
 * submit (arc 3 T9 / D4).
 *
 * The claim flow used to end in a dead end: the supplier submitted a revision
 * and that was the last thing that ever happened on that link. This is the
 * funnel seam — the one moment the supplier has just demonstrated they care
 * about their profile, which is when a durable account is worth offering.
 *
 * FOUR THINGS THIS DELIBERATELY DOES NOT DO:
 *
 *  1. It does not consume the claim token. `POST /api/portal/{token}/
 *     request-account` VALIDATES the token and leaves it usable, so the first
 *     door keeps working exactly as it did — a supplier who requests an
 *     account and then ignores the email has lost nothing.
 *  2. It does not claim the supplier is signed in. A link is being emailed;
 *     that is all that has happened, and the copy says only that.
 *  3. It does not distinguish a known address from an unknown one (D5). The
 *     endpoint answers 200 {"ok":true} for every outcome — account
 *     established, account already existed, member pending, unparseable
 *     email — and the confirmation here is one constant.
 *  4. It never uses the word "saved". The state this renders inside is the
 *     PENDING confirmation for a revision that is not live yet, and the
 *     pre-existing characterisation test asserts that word appears nowhere
 *     on it.
 *
 * Flag-gated: with NEXT_PUBLIC_SUPPLIER_SESSION_V1 off this renders nothing,
 * so the claim page is byte-identical to before arc 3.
 */

import { useState } from "react";
import { BRAND_NAME } from "@/lib/brand";
import { supplierSessionEnabled } from "@/lib/flags";

/** THE confirmation. One wording for every outcome the endpoint can have. */
const SENT_TITLE = "Check your email";
const SENT_BODY =
  "If that address can be set up, we've sent a sign-in link. Opening it signs you in.";

type Phase = "offer" | "form" | "sent" | "retry";

export function AccountBridge({ token }: { token: string }) {
  const [phase, setPhase] = useState<Phase>("offer");
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);

  if (!supplierSessionEnabled()) return null;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = email.trim();
    if (!trimmed || submitting) return;
    setSubmitting(true);
    let ok = false;
    try {
      const res = await fetch(
        `/api/portal/${encodeURIComponent(token)}/request-account`,
        {
          method: "POST",
          cache: "no-store",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email: trimmed }),
        },
      );
      ok = res.ok;
    } catch {
      ok = false;
    }
    setSubmitting(false);
    setPhase(ok ? "sent" : "retry");
  };

  if (phase === "sent") {
    return (
      <div className="supplier-bridge" data-testid="account-bridge">
        <p className="supplier-bridge-title">{SENT_TITLE}</p>
        <p className="supplier-bridge-body">{SENT_BODY}</p>
      </div>
    );
  }

  if (phase === "offer") {
    return (
      <div className="supplier-bridge" data-testid="account-bridge">
        <p className="supplier-bridge-title">
          Want to manage this yourself next time?
        </p>
        <p className="supplier-bridge-body">
          Create a {BRAND_NAME} account and you can see buyer requests, quote
          them, and update your profile whenever you like — no link needed.
        </p>
        <button
          type="button"
          className="portal-brand-add-btn"
          onClick={() => setPhase("form")}
        >
          Create your account
        </button>
      </div>
    );
  }

  return (
    <form className="supplier-bridge" onSubmit={submit} noValidate
          data-testid="account-bridge">
      <p className="supplier-bridge-title">Create your account</p>
      {phase === "retry" && (
        <p className="portal-soft-error" role="alert">
          We couldn&apos;t set that up just now. Please try again in a moment.
        </p>
      )}
      <label className="quote-label" htmlFor="bridge-email">
        Work email
      </label>
      <input
        id="bridge-email"
        className="quote-input"
        type="email"
        name="email"
        autoComplete="email"
        placeholder="you@yourcompany.com"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        disabled={submitting}
      />
      <p className="quote-hint">
        We&apos;ll email you a sign-in link. Nothing about this profile
        submission changes either way.
      </p>
      <button
        type="submit"
        className="portal-brand-add-btn"
        disabled={submitting || !email.trim()}
      >
        {submitting ? "Sending…" : "Email me a sign-in link"}
      </button>
    </form>
  );
}
