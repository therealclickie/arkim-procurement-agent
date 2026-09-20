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
 *
 * Sign-out (T10) lives here because it has to be reachable from every
 * authenticated surface. It revokes the session SERVER-side — clearing the
 * client's idea of who it is would be theatre, since the credential is an
 * httpOnly cookie only the server can invalidate.
 */

import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";
import type { SupplierSession } from "@/lib/use-supplier-session";
import type { SupplierCapability } from "@/lib/supplier-api";
import { SupplierSurface } from "./supplier-chrome";

export const LOGIN_PATH = "/supplier/login";

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
          <SignOutButton session={session} />
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


/**
 * Sign out: revoke the session on the server, then leave.
 *
 * The navigation happens whatever the call returns. A supplier who asked to
 * leave and was kept on an authenticated screen because a request failed
 * would reasonably conclude they are still signed in — and on a shared
 * machine that is the dangerous reading. The revoke is the control; the
 * redirect is the honest acknowledgement.
 */
function SignOutButton({ session }: { session: SupplierSession }) {
  const router = useRouter();
  const [leaving, setLeaving] = useState(false);
  return (
    <button
      type="button"
      className="supplier-link-btn"
      disabled={leaving}
      onClick={async () => {
        setLeaving(true);
        await session.logout();
        router.replace(LOGIN_PATH);
      }}
    >
      {leaving ? "Signing out…" : "Sign out"}
    </button>
  );
}
