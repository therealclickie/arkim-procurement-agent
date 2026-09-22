"use client";

/**
 * ProfileScreen — the supplier's own profile, under a session (arc 3 T7).
 *
 * The SAME `ProfileForm` the claim page renders, with the same tri-state
 * brand control and the same mappers. The form never knew about the token and
 * does not know about the session either — only its caller changes (D3).
 *
 * SELF-DECLARATION IS STILL A PROPOSAL. Signing in does not promote an edit
 * to a save: `POST /api/supplier/propose-revision` lands a PENDING review
 * item exactly as the token door does, and nothing writes the registry until
 * a concierge approves. So the confirmation says SUBMITTED FOR REVIEW, never
 * "saved" — the difference is the whole point of the supplier-proposes /
 * concierge-approves split, and a green "Saved!" here would be a lie about
 * what the buyer-facing data now says.
 *
 * The one thing a session adds over a token is provenance: the revision
 * records which member proposed it.
 */

import { useCallback, useEffect, useState } from "react";
import { BRAND_NAME } from "@/lib/brand";
import type { PortalProfile } from "@/lib/portal-api";
import { useSupplierMode } from "@/lib/supplier-mode";
import type { SupplierSession } from "@/lib/use-supplier-session";
import {
  ProfileForm,
  type FormState,
} from "@/app/portal/[token]/profile-form";
import {
  formToRevision,
  profileToForm,
} from "@/app/portal/[token]/claim-page";
import { PortalTeaser } from "@/app/portal/[token]/portal-states";
import { GoferLoader } from "@/components/ui/gofer-loader";
import { notificationsEnabled } from "@/lib/flags";
import { NotificationPreferences } from "./notification-preferences";
import { SupplierShell } from "../supplier-shell";

type Phase = "loading" | "ready" | "submitted" | "soft-error" | "unavailable";

export function ProfileScreen({ session }: { session: SupplierSession }) {
  const mode = useSupplierMode();
  const [phase, setPhase] = useState<Phase>("loading");
  const [profile, setProfile] = useState<PortalProfile | null>(null);
  // Form state lives here so it survives a failed submit — the supplier never
  // re-enters what they already typed.
  const [form, setForm] = useState<FormState | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void mode.getProfile().then((result) => {
      if (cancelled) return;
      if (result.ok) {
        setProfile(result.data);
        setForm(profileToForm(result.data));
        setPhase("ready");
      } else {
        // No profile for this account yet (the registry record has not been
        // created) — say so plainly rather than render an empty form that
        // would submit into nothing.
        setPhase("unavailable");
      }
    });
    return () => {
      cancelled = true;
    };
  }, [mode]);

  const handleSubmit = useCallback(
    async (nextForm: FormState) => {
      setForm(nextForm);
      setSubmitting(true);
      const result = await mode.proposeRevision(formToRevision(nextForm));
      setSubmitting(false);
      setPhase(result.ok ? "submitted" : "soft-error");
    },
    [mode],
  );

  const shell = (children: React.ReactNode) => (
    <SupplierShell session={session} current="/supplier/profile">
      {children}
    </SupplierShell>
  );

  if (phase === "loading") {
    return shell(
      <div className="portal-loading">
        <GoferLoader size={96} aria-label="Loading your supplier profile" />
        <p className="portal-loading-text">Loading your profile…</p>
      </div>,
    );
  }

  if (phase === "unavailable") {
    return shell(
      <section className="supplier-notice" aria-label="Profile unavailable">
        <h1 className="supplier-notice-title">
          Your profile isn&apos;t ready yet
        </h1>
        <div className="supplier-notice-body">
          <p>
            We couldn&apos;t load your company profile. Your {BRAND_NAME}{" "}
            representative can set it up — your requests are unaffected.
          </p>
        </div>
      </section>,
    );
  }

  if (phase === "submitted") {
    return shell(
      <section className="supplier-notice" aria-label="Submitted for review">
        <p className="portal-submitted-eyebrow">Submitted for review</p>
        <h1 className="supplier-notice-title">
          Thanks — your rep will review and confirm these changes
        </h1>
        <div className="supplier-notice-body">
          <p>
            Your edits are pending. Nothing changes on your profile until your{" "}
            {BRAND_NAME} representative approves them. We&apos;ll be in touch
            if anything needs clarifying.
          </p>
        </div>
      </section>,
    );
  }

  if (!profile || !form) return shell(null);

  return shell(
    <>
      <PortalTeaser teaser={profile.teaser} />
      {/* Arc 4 T11: the member's own delivery preference. Flag off ⇒ nothing
          renders here at all, and the profile page is as it was. */}
      {notificationsEnabled() && <NotificationPreferences />}
      {phase === "soft-error" && (
        <div className="portal-soft-error" role="alert" aria-live="polite">
          We couldn&apos;t submit your changes right now. Your edits are kept —
          please try again in a moment.
        </div>
      )}
      <ProfileForm
        initial={form}
        aftermarketDisclosure={profile.aftermarket_disclosure}
        onChange={setForm}
        onSubmit={handleSubmit}
        submitting={submitting}
      />
    </>,
  );
}
