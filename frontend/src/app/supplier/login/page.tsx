"use client";

/**
 * /supplier/login — the route.
 *
 * A thin flag gate over LoginScreen. With NEXT_PUBLIC_SUPPLIER_SESSION_V1 off
 * the route renders nothing at all: no form, no heading, no network call.
 * That inertness has to live in the page, because a route (unlike a section
 * inside an existing page) has no backend call to 404 on before first paint.
 *
 * NOTE (G1): this is a `"use client"` component with no dynamic segment, so
 * there is no `params` Promise and no `use()` — it renders under the
 * installed React 18.3.1 and is therefore fully render-testable, including
 * at both flag values. No React 19 bump is implied by this arc.
 */

import { supplierSessionEnabled } from "@/lib/flags";
import { LoginScreen } from "./login-screen";

export default function SupplierLoginPage() {
  if (!supplierSessionEnabled()) return null;
  return <LoginScreen />;
}
