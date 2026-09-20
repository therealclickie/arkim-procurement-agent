"use client";

/** /supplier/profile — the session profile. Flag-gated inert, then guarded. */

import { supplierSessionEnabled } from "@/lib/flags";
import { SessionGuard } from "../session-guard";
import { ProfileScreen } from "./profile-screen";

export default function SupplierProfilePage() {
  if (!supplierSessionEnabled()) return null;
  return (
    <SessionGuard>{(session) => <ProfileScreen session={session} />}</SessionGuard>
  );
}
