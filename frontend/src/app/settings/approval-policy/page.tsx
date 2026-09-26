"use client";

import { buyerSessionEnabled } from "@/lib/flags";
import { ApprovalPolicyScreen } from "./approval-policy-screen";

/** /settings/approval-policy — arc 6 D5. Absent with the buyer session flag off. */
export default function ApprovalPolicyPage() {
  if (!buyerSessionEnabled()) return null;
  return <ApprovalPolicyScreen />;
}
