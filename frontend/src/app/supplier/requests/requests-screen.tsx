"use client";

/**
 * RequestsScreen — the supplier's RFQ inbox, served by a session (arc 3 T6).
 *
 * The same `OpenRequests` component the claim page renders, fetching through
 * the session door instead of a token. It is not a fork and not a copy: one
 * component, two auth modes (D3).
 *
 * WHERE THE EMPTY STATE LIVES, and why it matters. `OpenRequests` renders
 * literally nothing when a supplier has no open RFQs and no quote history —
 * a deliberate property of the claim page (no empty shells there) that
 * pre-existing tests pin. A blank page would be a poor inbox, so the honest
 * empty-state copy is HERE, in the page, not in the component. Moving it into
 * the component would break the claim surface.
 *
 * "Honest" means: it says there is nothing right now. It does not invent a
 * sample row, a placeholder count, or an encouraging number.
 */

import { useState } from "react";
import { BRAND_NAME } from "@/lib/brand";
import { OpenRequests } from "@/app/portal/[token]/open-requests";
import type { SupplierSession } from "@/lib/use-supplier-session";
import { SupplierShell } from "../supplier-shell";

export function RequestsScreen({ session }: { session: SupplierSession }) {
  // `null` = the first load has not settled. Distinguished from `true` so the
  // empty-state copy never flashes before the data arrives.
  const [isEmpty, setIsEmpty] = useState<boolean | null>(null);

  return (
    <SupplierShell session={session} current="/supplier/requests">
      <div>
        <p className="portal-form-eyebrow">Your requests</p>
        <h1 className="portal-form-heading">Open requests</h1>
        <p className="portal-form-sub">
          Buyer requests {BRAND_NAME} has sent to{" "}
          {session.account?.supplier_domain}. Quote one and it goes straight
          back to the buyer.
        </p>
      </div>

      <OpenRequests onEmpty={setIsEmpty} />

      {isEmpty === true && (
        <section className="supplier-notice" aria-label="No open requests">
          <h2 className="supplier-notice-title">No open requests right now</h2>
          <div className="supplier-notice-body">
            <p>
              When a buyer asks for a part you can supply, it appears here and
              we email you. Keeping your profile current is what gets you
              matched.
            </p>
          </div>
        </section>
      )}
    </SupplierShell>
  );
}
