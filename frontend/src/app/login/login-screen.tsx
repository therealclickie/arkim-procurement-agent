"use client";

/**
 * Buyer LoginScreen (arc 6) — request a magic sign-in link.
 *
 * THE ONE RULE (as for suppliers): the response is identical whether or not
 * the address belongs to a member. The backend answers one 200 for a member,
 * a stranger, a revoked member and a throttled request alike; this screen
 * shows ONE confirmation rendered from ONE constant, and validates nothing but
 * emptiness, so it cannot build an oracle the backend refused to build.
 *
 * Membership is by invitation only (D3): there is no "create an account" path
 * here, and no domain auto-join.
 */

import { useState } from "react";
import { BRAND_NAME } from "@/lib/brand";
import { requestBuyerLink } from "@/lib/buyer-api";
import { SupplierNotice, SupplierSurface } from "../supplier/supplier-chrome";

/** THE confirmation. One constant, one wording, every outcome. */
export const SENT_TITLE = "Check your email";
const SENT_BODY =
  "If that address belongs to a member of your company's account, we've sent a sign-in link. It expires in 30 minutes and can be used once.";
const RETRY_MESSAGE = "We couldn't send a link just now. Please wait a moment and try again.";

type Phase = "form" | "sent" | "retry";

export function BuyerLoginScreen() {
  const [email, setEmail] = useState("");
  const [phase, setPhase] = useState<Phase>("form");
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = email.trim();
    if (!trimmed || submitting) return;
    setSubmitting(true);
    const result = await requestBuyerLink(trimmed);
    setSubmitting(false);
    setPhase(result.ok ? "sent" : "retry");
  };

  if (phase === "sent") {
    return (
      <SupplierSurface>
        <SupplierNotice title={SENT_TITLE} body={<p>{SENT_BODY}</p>}>
          <p className="quote-hint">
            <button type="button" className="supplier-link-btn" onClick={() => setPhase("form")}>
              Use a different address
            </button>
          </p>
        </SupplierNotice>
      </SupplierSurface>
    );
  }

  return (
    <SupplierSurface>
      <form className="quote-form" onSubmit={handleSubmit} noValidate>
        <div>
          <p className="portal-form-eyebrow">Sign in</p>
          <h1 className="portal-form-heading">Sign in to {BRAND_NAME}</h1>
          <p className="portal-form-sub">
            Enter your work email and we&apos;ll send you a sign-in link. No password to remember.
          </p>
        </div>

        {phase === "retry" && (
          <div className="portal-soft-error" role="alert" aria-live="polite">
            {RETRY_MESSAGE}
          </div>
        )}

        <div className="quote-field">
          <label className="quote-label" htmlFor="buyer-email">Work email</label>
          <input
            id="buyer-email"
            className="quote-input"
            type="email"
            name="email"
            autoComplete="email"
            inputMode="email"
            placeholder="you@yourcompany.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={submitting}
          />
        </div>

        <div className="portal-form-actions">
          <button type="submit" className="portal-submit" disabled={submitting || !email.trim()}>
            {submitting ? "Sending…" : "Email me a sign-in link"}
          </button>
        </div>

        <p className="quote-hint">
          Accounts are set up by invitation. If you don&apos;t have one, ask your company&apos;s
          {" "}{BRAND_NAME} admin to invite you.
        </p>
      </form>
    </SupplierSurface>
  );
}
