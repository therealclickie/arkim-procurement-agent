"use client";

/**
 * VerifyScreen — the magic-link landing (arc 3 T5).
 *
 * The emailed link is `/supplier/verify?token=<raw>`. This screen reads the
 * token from the query string, exchanges it for a session, and sends the
 * supplier to their inbox.
 *
 * WHAT HAPPENS TO THE TOKEN. It is read from the URL into a local variable,
 * passed to `verifyMagicLink`, and dropped. It is never put in state that
 * renders, never written to localStorage / sessionStorage / document.cookie,
 * never logged. The session that replaces it is an httpOnly cookie the
 * backend sets on the verify response — this file never sees it either (D1).
 * The redirect uses `router.replace`, so the token-bearing URL does not stay
 * in history for a back-button to resurrect.
 *
 * WHY THERE IS ONLY ONE FAILURE STATE. The backend answers unknown, expired,
 * already-used, PENDING-member and store-error with one identical 401, and
 * `verifyMagicLink` collapses every outcome to a boolean. So there is no
 * failure kind available to branch on even if we wanted to — which is the
 * point: "this link was already used" would tell an attacker their guess was
 * a real token, and "your account is pending" would tell a pending member
 * something the backend deliberately withholds.
 *
 * G1: a param-free client component reading `useSearchParams()`, not a server
 * component reading a `searchParams` Promise — so no `use()`, so no React 19
 * dependency, so this route is fully render-testable under React 18.3.1.
 */

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { BRAND_NAME } from "@/lib/brand";
import { verifyMagicLink } from "@/lib/supplier-api";
import { SupplierNotice, SupplierSurface } from "../supplier-chrome";

/** Where a verified supplier lands. */
export const INBOX_PATH = "/supplier/requests";
export const LOGIN_PATH = "/supplier/login";

/** THE rejection. One wording for every failure mode. */
const REJECT_TITLE = "This sign-in link is no longer valid";
const REJECT_BODY =
  "Sign-in links expire and can only be used once. Request a new one and we'll email it to you.";

type Phase = "verifying" | "rejected";

export function VerifyScreen() {
  const router = useRouter();
  const params = useSearchParams();
  const [phase, setPhase] = useState<Phase>("verifying");
  // Guards against React 18 StrictMode's double-invoked effect burning the
  // single-use token twice — the second exchange would legitimately 401 and
  // reject a link that had just worked.
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    // Read and drop. Nothing downstream of this line holds the token.
    const token = params?.get("token") ?? "";
    if (!token) {
      // No token in the URL is the same dead end as a bad one — a visitor who
      // typed the path by hand learns nothing a link-holder wouldn't.
      setPhase("rejected");
      return;
    }
    void verifyMagicLink(token).then((ok) => {
      if (ok) {
        // replace, not push: the token-bearing URL leaves history.
        router.replace(INBOX_PATH);
      } else {
        setPhase("rejected");
      }
    });
  }, [params, router]);

  if (phase === "verifying") {
    return (
      <SupplierSurface>
        <SupplierNotice
          title="Signing you in…"
          body={<p>One moment while we check your link.</p>}
        />
      </SupplierSurface>
    );
  }

  return (
    <SupplierSurface>
      <SupplierNotice
        title={REJECT_TITLE}
        body={
          <>
            <p>{REJECT_BODY}</p>
            <p>
              If you keep getting this, your {BRAND_NAME} representative can
              help.
            </p>
          </>
        }
      >
        <p>
          <Link href={LOGIN_PATH} className="supplier-link">
            Request a new sign-in link
          </Link>
        </p>
      </SupplierNotice>
    </SupplierSurface>
  );
}
