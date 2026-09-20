"use client";

/**
 * LoginScreen — request a magic sign-in link (arc 3 T4).
 *
 * D5, THE ONE RULE THIS FILE EXISTS TO HOLD: the response is identical
 * whether or not the address is known.
 *
 * The backend already guarantees this — `request-link` answers
 * 200 {"ok":true} for a known ACTIVE member, an unknown email, an address
 * with no account, a PENDING member and an unparseable string alike, and it
 * rate-limits BEFORE any lookup so throttling is not an oracle either. The
 * only way to leak existence from here is for the UI to invent a distinction
 * the backend refused to make. So:
 *
 *   - ONE confirmation, rendered from a single constant. Not a template with
 *     an interpolated branch, not two strings that happen to match today.
 *   - No "account not found", no "check your spam folder" (which would imply
 *     a send definitely happened), no "we've emailed sales@..." echo.
 *   - No client-side "is this a real company domain?" validation. Rejecting
 *     an address before the request would be an oracle built entirely in the
 *     browser. Only emptiness is checked, which reveals nothing.
 *
 * A 429 is shown as a retry prompt. That is not an existence signal: the
 * limiter's buckets are (email) and (ip), both incremented before any account
 * lookup, so a known and an unknown address trip it identically.
 */

import { useState } from "react";
import Link from "next/link";
import { BRAND_NAME } from "@/lib/brand";
import { requestMagicLink } from "@/lib/supplier-api";
import { SupplierNotice, SupplierSurface } from "../supplier-chrome";

/** THE confirmation. One constant, one wording, every outcome. */
const SENT_TITLE = "Check your email";
const SENT_BODY =
  "If that address is on file, we've sent a sign-in link. It expires shortly, and can be used once.";

/** Shown only when the request itself could not be made. Says nothing about
 *  the address — a rate limit and a network failure are both "not now". */
const RETRY_MESSAGE =
  "We couldn't send a link just now. Please wait a moment and try again.";

type Phase = "form" | "sent" | "retry";

export function LoginScreen() {
  const [email, setEmail] = useState("");
  const [phase, setPhase] = useState<Phase>("form");
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = email.trim();
    if (!trimmed || submitting) return;
    setSubmitting(true);
    const result = await requestMagicLink(trimmed);
    setSubmitting(false);
    // `ok` covers every backend outcome that reached the server, which is
    // every outcome that could distinguish a known address from an unknown
    // one. They all land here.
    setPhase(result.ok ? "sent" : "retry");
  };

  if (phase === "sent") {
    return (
      <SupplierSurface>
        <SupplierNotice title={SENT_TITLE} body={<p>{SENT_BODY}</p>}>
          <p className="quote-hint">
            Didn&apos;t get it?{" "}
            <button
              type="button"
              className="supplier-link-btn"
              onClick={() => setPhase("form")}
            >
              Try a different address
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
          <p className="portal-form-eyebrow">Supplier sign-in</p>
          <h1 className="portal-form-heading">Sign in to {BRAND_NAME}</h1>
          <p className="portal-form-sub">
            Enter your work email and we&apos;ll send you a sign-in link. No
            password to remember.
          </p>
        </div>

        {phase === "retry" && (
          <div className="portal-soft-error" role="alert" aria-live="polite">
            {RETRY_MESSAGE}
          </div>
        )}

        <div className="quote-field">
          <label className="quote-label" htmlFor="supplier-email">
            Work email
          </label>
          <input
            id="supplier-email"
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
          <button
            type="submit"
            className="portal-submit"
            disabled={submitting || !email.trim()}
          >
            {submitting ? "Sending…" : "Email me a sign-in link"}
          </button>
        </div>

        <p className="quote-hint">
          Have a link from {BRAND_NAME} already? Open it and you&apos;ll be
          signed in. Questions go to your {BRAND_NAME} representative — we
          never ask for a password.{" "}
          <Link href="/" className="supplier-link">
            Back to {BRAND_NAME}
          </Link>
        </p>
      </form>
    </SupplierSurface>
  );
}
