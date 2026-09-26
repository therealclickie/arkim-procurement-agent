"use client";

import { buyerSessionEnabled } from "@/lib/flags";
import { TeamScreen } from "./team-screen";

/** /team — arc 6 member management. Absent with the buyer session flag off. */
export default function TeamPage() {
  if (!buyerSessionEnabled()) return null;
  return <TeamScreen />;
}
