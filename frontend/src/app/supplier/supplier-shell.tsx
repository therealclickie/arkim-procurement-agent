"use client";

/**
 * SupplierShell — the authenticated chrome shared by the session routes.
 *
 * Header carries the two-level identity arc 2 established: the ACCOUNT (the
 * company) and the MEMBER (the person). Both are shown because they are
 * different facts and a supplier with several colleagues needs to know which
 * one they are signed in as.
 *
 * The nav reflects capabilities (D6): a link to a surface the member cannot
 * use is hidden. Hiding is a courtesy — the server 403s regardless, and a
 * member who types the URL gets the same refusal.
 */

import Link from "next/link";
import type { SupplierSession } from "@/lib/use-supplier-session";
import type { SupplierCapability } from "@/lib/supplier-api";
import { SupplierSurface } from "./supplier-chrome";

interface NavItem {
  href: string;
  label: string;
  /** Hidden unless the member holds this capability. Undefined = always shown. */
  capability?: SupplierCapability;
}

const NAV: NavItem[] = [
  { href: "/supplier/requests", label: "Requests" },
  { href: "/supplier/profile", label: "Profile" },
  { href: "/supplier/members", label: "Team", capability: "view_members" },
];

export function SupplierShell({
  session,
  current,
  actions,
  children,
}: {
  session: SupplierSession;
  /** The active path, for `aria-current`. */
  current: string;
  /** Extra header controls (T10's sign-out lives here). */
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  const { account, member, can } = session;
  const items = NAV.filter((i) => !i.capability || can(i.capability));

  return (
    <SupplierSurface
      aside={
        <span className="supplier-identity">
          <span className="supplier-identity-account">
            {account?.supplier_domain}
          </span>
          <span className="supplier-identity-email">{member?.email}</span>
          {actions}
        </span>
      }
    >
      <nav className="supplier-nav" aria-label="Supplier sections">
        {items.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            aria-current={item.href === current ? "page" : undefined}
          >
            {item.label}
          </Link>
        ))}
      </nav>
      {children}
    </SupplierSurface>
  );
}
