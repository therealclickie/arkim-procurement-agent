"use client";

/**
 * /supplier/verify — the route the emailed magic link points at
 * (`utils/supplier_accounts.py` mints `{base}/supplier/verify?token=...`).
 *
 * Flag-gated inert, like every arc-3 route: off ⇒ renders nothing and makes
 * no request, so a link that arrives before the surface is enabled dead-ends
 * silently rather than half-working.
 *
 * The Suspense boundary is Next's requirement for `useSearchParams()` in a
 * client component — without it the route opts the whole page out of static
 * rendering at build time.
 */

import { Suspense } from "react";
import { supplierSessionEnabled } from "@/lib/flags";
import { VerifyScreen } from "./verify-screen";

export default function SupplierVerifyPage() {
  if (!supplierSessionEnabled()) return null;
  return (
    <Suspense fallback={null}>
      <VerifyScreen />
    </Suspense>
  );
}
