"use client";

/**
 * Buyer session (arc 6) — "who am I" for the buyer surfaces, the guard that
 * sends a signed-out visitor to /login, and the permission gate controls use.
 *
 * NOT A SECURITY BOUNDARY. The session is an httpOnly cookie this code cannot
 * read; the only way to know whether one exists is to ask the server
 * (`GET /api/buyer/me`). What is hidden here is hidden for the user's sake —
 * a Requester is not shown an Approve button they cannot use. Every gated
 * endpoint re-checks the permission matrix server-side, so a user who edits
 * the permission list in a debugger gains a visible button and a 403.
 *
 * FLAG OFF (NEXT_PUBLIC_BUYER_SESSION_V1 unset) there is no provider, and
 * `useBuyerCan` answers true for everything: today's no-login UI, unchanged.
 */

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import {
  getBuyerMe,
  logoutBuyer,
  type BuyerCapability,
  type BuyerCompany,
  type BuyerMe,
} from "@/lib/buyer-api";
import { GoferLoader } from "@/components/ui/gofer-loader";
import { buyerSessionEnabled } from "@/lib/flags";

export const BUYER_LOGIN_PATH = "/login";

export interface BuyerSession {
  company: BuyerCompany;
  member: BuyerMe["member"];
  can: (capability: BuyerCapability) => boolean;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
}

const BuyerSessionContext = createContext<BuyerSession | null>(null);

/** The signed-in buyer, or null (flag off, or outside a guarded surface). */
export function useBuyerSession(): BuyerSession | null {
  return useContext(BuyerSessionContext);
}

/** Should this capability's controls be SHOWN? True when no buyer identity is
 *  in force (flag off — today's behaviour); otherwise THE server matrix, as
 *  reflected by `/me`. Display only — the server enforces. */
export function useBuyerCan(): (capability: BuyerCapability) => boolean {
  const session = useContext(BuyerSessionContext);
  return useCallback(
    (capability: BuyerCapability) => (session ? session.can(capability) : true),
    [session],
  );
}

/** Render `children` only when the capability is held (see useBuyerCan). */
export function BuyerCan({ capability, children, fallback = null }: {
  capability: BuyerCapability;
  children: ReactNode;
  fallback?: ReactNode;
}) {
  const can = useBuyerCan();
  return <>{can(capability) ? children : fallback}</>;
}

/** For tests and for the guard: provide a known session. */
export function BuyerSessionProvider({ session, children }: {
  session: BuyerSession;
  children: ReactNode;
}) {
  return <BuyerSessionContext.Provider value={session}>{children}</BuyerSessionContext.Provider>;
}

/** Build a session value from a `/me` payload. */
export function sessionFromMe(
  me: BuyerMe,
  refresh: () => Promise<void> = async () => {},
  logout: () => Promise<void> = async () => {},
): BuyerSession {
  const perms = new Set(me.member.permissions ?? []);
  return {
    company: me.company,
    member: me.member,
    can: (c) => perms.has(c),
    refresh,
    logout,
  };
}

/** A layout-level gate for buyer frames that are not ProcShell (the legacy
 *  /runs frame): the guard with the flag on, a pass-through with it off. */
export function BuyerGate({ children }: { children: ReactNode }) {
  if (!buyerSessionEnabled()) return <>{children}</>;
  return <BuyerSessionGuard>{children}</BuyerSessionGuard>;
}

type GuardState =
  | { kind: "loading" }
  | { kind: "unauthorized" }
  | { kind: "ok"; me: BuyerMe };

/**
 * BuyerSessionGuard — wraps every buyer route when the flag is on. Loading ⇒ a
 * loader; no session ⇒ replace to /login (render nothing meanwhile — never a
 * half-authenticated shell); a session ⇒ provide it to everything beneath.
 */
export function BuyerSessionGuard({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [state, setState] = useState<GuardState>({ kind: "loading" });

  const load = useCallback(async () => {
    const res = await getBuyerMe();
    setState(res.ok ? { kind: "ok", me: res.data } : { kind: "unauthorized" });
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (state.kind === "unauthorized") router.replace(BUYER_LOGIN_PATH);
  }, [state.kind, router]);

  const logout = useCallback(async () => {
    await logoutBuyer();
    // Clear regardless of the call's outcome: the cookie is httpOnly, so the
    // client cannot confirm it is gone, and stale identity on screen after the
    // user asked to leave is the worse failure.
    setState({ kind: "unauthorized" });
  }, []);

  if (state.kind === "loading") {
    return (
      <div className="portal-loading" style={{ position: "absolute", inset: 0 }}>
        <GoferLoader size={96} aria-label="Loading your account" />
      </div>
    );
  }
  if (state.kind === "unauthorized") return null;
  return (
    <BuyerSessionProvider session={sessionFromMe(state.me, load, logout)}>
      {children}
    </BuyerSessionProvider>
  );
}
