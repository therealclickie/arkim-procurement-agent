"use client";

/**
 * Buyer VerifyScreen (arc 6) — the landing for `/verify?token=<raw>`.
 *
 * AN EXPLICIT CLICK, NOT A LOAD — arc 4b's lesson on the supplier page.
 * Corporate link scanners pre-fetch URLs at the recipient's mail gateway; a
 * page that exchanged the token on mount would have it consumed by a scanner
 * before the person ever clicked. A gesture is the one thing a scanner cannot
 * produce, so nothing happens until "Continue to sign in" is pressed.
 *
 * THE TOKEN is read from the URL into a local, exchanged, and dropped: never
 * rendered, never stored in localStorage / sessionStorage / document.cookie,
 * never logged. The session it becomes is an httpOnly cookie this file never
 * sees. `router.replace` keeps the token-bearing URL out of history.
 *
 * ONE failure state for every failure (unknown, expired, used, revoked, no
 * token at all), because the backend deliberately collapses them into one 401.
 */

import { useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { verifyBuyerLink } from "@/lib/buyer-api";
import { SupplierNotice, SupplierSurface } from "../supplier/supplier-chrome";

export const HOME_PATH = "/";
export const LOGIN_PATH = "/login";
export const CONTINUE_LABEL = "Continue to sign in";
export const REJECT_TITLE = "This sign-in link is no longer valid";

type Phase = "idle" | "verifying" | "rejected";

export function BuyerVerifyScreen() {
  const router = useRouter();
  const params = useSearchParams();
  const [phase, setPhase] = useState<Phase>("idle");
  // Single-use token: a double click must not spend it twice.
  const started = useRef(false);

  const onContinue = () => {
    if (started.current) return;
    started.current = true;
    const token = params?.get("token") ?? "";
    if (!token) {
      setPhase("rejected");
      return;
    }
    setPhase("verifying");
    void verifyBuyerLink(token).then((ok) => {
      if (ok) router.replace(HOME_PATH);
      else setPhase("rejected");
    });
  };

  if (phase === "idle") {
    return (
      <SupplierSurface>
        <SupplierNotice
          title="Finish signing in"
          body={<p>Sign-in links can only be used once, so we wait for you before using this one.</p>}
        >
          <p>
            <button type="button" className="supplier-link-btn" onClick={onContinue}>
              {CONTINUE_LABEL}
            </button>
          </p>
        </SupplierNotice>
      </SupplierSurface>
    );
  }

  if (phase === "verifying") {
    return (
      <SupplierSurface>
        <SupplierNotice title="Signing you in…" body={<p>One moment while we check your link.</p>} />
      </SupplierSurface>
    );
  }

  return (
    <SupplierSurface>
      <SupplierNotice
        title={REJECT_TITLE}
        body={<p>Sign-in links expire and can only be used once. Request a new one and we&apos;ll email it to you.</p>}
      >
        <p>
          <Link href={LOGIN_PATH} className="supplier-link">Request a new sign-in link</Link>
        </p>
      </SupplierNotice>
    </SupplierSurface>
  );
}
