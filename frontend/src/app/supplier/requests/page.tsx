"use client";

/**
 * /supplier/requests — the session RFQ inbox.
 *
 * Flag-gated inert, then session-guarded. The guard is here rather than in
 * `supplier/layout.tsx` because /supplier/login and /supplier/verify share
 * that layout and must stay reachable without a session.
 */

import { supplierSessionEnabled } from "@/lib/flags";
import { SessionGuard } from "../session-guard";
import { RequestsScreen } from "./requests-screen";

export default function SupplierRequestsPage() {
  if (!supplierSessionEnabled()) return null;
  return (
    <SessionGuard>
      {(session) => <RequestsScreen session={session} />}
    </SessionGuard>
  );
}
