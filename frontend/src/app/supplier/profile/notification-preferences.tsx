"use client";

/**
 * NotificationPreferences — the member's own delivery preference (arc 4 T11/D7).
 *
 * SELF ONLY, and there is no member id anywhere in this component: the server
 * reads it from the session. So there is nothing here an admin could point at
 * someone else, and nothing a tampered request could aim elsewhere.
 *
 * FLAG POSTURE. The caller renders this only when
 * `NEXT_PUBLIC_NOTIFICATIONS_V1` is on, and the component ALSO returns null
 * when the backend answers anything but 200 — with the backend flag off the
 * endpoint 404s, so a build where only the frontend flag is on shows nothing
 * rather than a control that cannot save.
 *
 * WHY IT SAVES ON CHANGE AND SAYS SO. A separate "Save" button is one more
 * thing to forget; a radio that silently does nothing until you press
 * something else is the worst of both. So the change is submitted immediately
 * and the outcome is stated — "Saved" or a soft error that keeps the previous
 * value visible. A failed save reverts the control, because leaving the new
 * value selected would tell the supplier they had turned notifications down
 * when they had not.
 */

import { useCallback, useEffect, useState } from "react";
import {
  getNotificationPreferences,
  setNotificationPreference,
  type NotificationPreference,
} from "@/lib/supplier-api";

/** The server owns the vocabulary; these are the labels for it. An unknown
 *  value from the server renders verbatim rather than being dropped. */
const LABEL: Record<string, string> = {
  IMMEDIATE: "Email me as soon as a request arrives",
  DAILY_DIGEST: "One summary email a day",
  NONE: "No request emails",
};

const HINT: Record<string, string> = {
  IMMEDIATE: "The fastest way to hear about a new quote request.",
  DAILY_DIGEST: "Everything from the day, batched into one email.",
  NONE: "You'll still get sign-in links and account emails.",
};

type Phase = "loading" | "ready" | "unavailable";

export function NotificationPreferences() {
  const [phase, setPhase] = useState<Phase>("loading");
  const [preference, setPreference] = useState<NotificationPreference | null>(null);
  const [choices, setChoices] = useState<NotificationPreference[]>([]);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void getNotificationPreferences().then((result) => {
      if (cancelled) return;
      if (result.ok) {
        setPreference(result.data.preference);
        setChoices(result.data.choices);
        setPhase("ready");
      } else {
        // Backend flag off (404) or session gone (401): render nothing. A
        // control that cannot save is worse than no control.
        setPhase("unavailable");
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const choose = useCallback(
    async (next: NotificationPreference) => {
      const previous = preference;
      setPreference(next);
      setSaving(true);
      setSaved(false);
      setFailed(false);
      const result = await setNotificationPreference(next);
      setSaving(false);
      if (result.ok) {
        setPreference(result.data.preference);
        setSaved(true);
      } else {
        // Revert: showing the new value after a failed save would tell the
        // supplier they had changed something they had not.
        setPreference(previous);
        setFailed(true);
      }
    },
    [preference],
  );

  if (phase !== "ready" || preference === null) return null;

  return (
    <section
      className="supplier-notification-prefs"
      aria-label="Email notifications"
    >
      <h2 className="portal-section-title">Email notifications</h2>
      <p className="quote-hint">
        How you hear about new quote requests. This is your own setting —
        colleagues on this account choose their own.
      </p>
      <fieldset className="supplier-pref-fieldset" disabled={saving}>
        <legend className="sr-only">Email notifications</legend>
        {choices.map((choice) => (
          <label key={choice} className="supplier-pref-option">
            <input
              type="radio"
              name="notification-preference"
              value={choice}
              checked={preference === choice}
              onChange={() => void choose(choice)}
            />
            <span className="supplier-pref-label">
              {LABEL[choice] ?? choice}
            </span>
            {HINT[choice] && (
              <span className="quote-hint">{HINT[choice]}</span>
            )}
          </label>
        ))}
      </fieldset>
      {saved && (
        <p className="portal-saved-note" role="status" aria-live="polite">
          Saved
        </p>
      )}
      {failed && (
        <div className="portal-soft-error" role="alert" aria-live="polite">
          We couldn&apos;t save that just now — your setting is unchanged.
          Please try again in a moment.
        </div>
      )}
    </section>
  );
}
