"use client";

/** /supplier/members — the account's team. Flag-gated inert, then guarded. */

import { supplierSessionEnabled } from "@/lib/flags";
import { SessionGuard } from "../session-guard";
import { MembersScreen } from "./members-screen";

export default function SupplierMembersPage() {
  if (!supplierSessionEnabled()) return null;
  return (
    <SessionGuard>{(session) => <MembersScreen session={session} />}</SessionGuard>
  );
}
