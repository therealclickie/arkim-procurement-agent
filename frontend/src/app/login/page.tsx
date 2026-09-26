"use client";

/**
 * /login — the buyer sign-in page (arc 6). Outside ProcShell on purpose: it
 * must be reachable with no session. Flag off ⇒ renders nothing and makes no
 * request (the buyer UI has no login today).
 */

import { buyerSessionEnabled } from "@/lib/flags";
import { BuyerLoginScreen } from "./login-screen";

export default function BuyerLoginPage() {
  if (!buyerSessionEnabled()) return null;
  return <BuyerLoginScreen />;
}
