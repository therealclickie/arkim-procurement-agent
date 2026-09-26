"use client";

/**
 * /verify — where the buyer's emailed sign-in link points
 * (`utils/buyer_accounts.magic_link_url`: `{base}/verify?token=...`). Outside
 * ProcShell so it is reachable with no session. Flag off ⇒ renders nothing.
 * Suspense is Next's requirement for `useSearchParams()` in a client page.
 */

import { Suspense } from "react";
import { buyerSessionEnabled } from "@/lib/flags";
import { BuyerVerifyScreen } from "./verify-screen";

export default function BuyerVerifyPage() {
  if (!buyerSessionEnabled()) return null;
  return (
    <Suspense fallback={null}>
      <BuyerVerifyScreen />
    </Suspense>
  );
}
