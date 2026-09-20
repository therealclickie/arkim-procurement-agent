/**
 * Supplier SESSION API client — the authenticated sibling of lib/portal-api.ts
 * (arc 3, NEXT_PUBLIC_SUPPLIER_SESSION_V1 + backend SUPPLIER_ACCOUNTS_V1).
 *
 * THE CREDENTIAL IS NOT IN THIS FILE, and that is the whole point (D1).
 * The session is an httpOnly cookie the backend set on verify. This module
 * never reads it, never writes it, never sees it — it only sets
 * `credentials: "include"` so the browser attaches it. There is no token in
 * localStorage, sessionStorage, document.cookie or any URL, which is the same
 * posture arc 1 locked in for the public surfaces, carried forward rather
 * than relaxed for convenience.
 *
 * Consequences worth stating, because they look like omissions otherwise:
 *  - No Authorization header is ever sent. The bearer form of a session still
 *    exists for API clients; a browser must not use it.
 *  - There is nothing to "log out of" client-side beyond dropping cached
 *    state — logout is a server call that revokes the session and clears the
 *    cookie.
 *
 * Outcome model mirrors portal-api's uniform `rejected`, with ONE addition:
 * 401 is distinguishable as `unauthorized`. That is not an enumeration oracle
 * — it says "your session is gone", about a session the caller already had,
 * and the UI needs it to send the user back to login rather than render an
 * indefinite empty page.
 */

import type {
  OpenRequest,
  PortalProfile,
  PortalQuoteBody,
  PortalQuoteSubmitResponse,
  ProposeRevisionBody,
  ProposeRevisionResponse,
  QuoteHistoryRow,
} from "@/lib/portal-api";

// ---------------------------------------------------------------------------
// Types — mirror api_server.py's /api/supplier/* shapes
// ---------------------------------------------------------------------------

export type SupplierCapability =
  | "view_requests"
  | "submit_quotes"
  | "propose_revisions"
  | "view_members"
  | "manage_members"
  | "change_roles"
  | "transfer_ownership";

export type SupplierRole = "OWNER" | "ADMIN" | "MEMBER";

export interface SupplierAccount {
  id: string;
  supplier_domain: string;
  status: string;
  created_at: string;
}

export interface SupplierMember {
  id: string;
  email: string;
  role: SupplierRole;
  status: string;
  /** Computed server-side from the capability matrix. Reflected by the UI;
   *  enforced by the server (D6) — a forged list buys nothing. */
  permissions: SupplierCapability[];
}

export interface SupplierMe {
  account: SupplierAccount;
  member: SupplierMember;
}

/** One row of GET /api/supplier/members. */
export interface AccountMember {
  id: string;
  email: string;
  registrable_domain: string | null;
  role: SupplierRole;
  status: string;
  created_at: string;
}

export interface VerifyResponse {
  token: string;
  expires_at: string;
}

// ---------------------------------------------------------------------------
// Outcome model
// ---------------------------------------------------------------------------

export type SessionResult<T> =
  | { ok: true; data: T }
  | { ok: false; unauthorized: true }
  /**
   * `status` is present ONLY for the member-management calls, which opt in
   * (see `withStatus` below). Everywhere else the rejection stays uniform —
   * a data surface that branched on status codes would be re-deriving
   * failure kinds the backend deliberately collapsed.
   */
  | { ok: false; rejected: true; status?: number };

export const UNAUTHORIZED = { ok: false, unauthorized: true } as const;
export const REJECTED = { ok: false, rejected: true } as const;

// ---------------------------------------------------------------------------
// Fetch primitive
// ---------------------------------------------------------------------------

/**
 * Fetch a session-scoped resource. `credentials: "include"` is the ONLY auth
 * mechanism — the httpOnly cookie rides along and no script ever touches it.
 *
 * Deliberately a separate primitive from portal-api's `portalFetch` rather
 * than a flag inside it: adding a `credentials` key to the shared primitive
 * would change every token-mode request, and token-mode request parity is a
 * hard requirement of this arc.
 */
async function sessionFetch<T>(
  path: string,
  init?: RequestInit,
  /**
   * Carry the HTTP status on a rejection. Opted into ONLY by member
   * management, where the caller is an already-authenticated admin acting on
   * their own account: there is no enumeration concern inside a company you
   * already belong to, and "that person is already a member" is far more
   * useful than "something went wrong". Every other surface keeps the
   * uniform rejection.
   */
  withStatus = false,
): Promise<SessionResult<T>> {
  try {
    const res = await fetch(`/api/supplier${path}`, {
      cache: "no-store",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers ?? {}),
      },
      ...init,
    });
    if (res.status === 401) return UNAUTHORIZED;
    if (!res.ok) {
      return withStatus ? { ...REJECTED, status: res.status } : REJECTED;
    }
    const data = (await res.json()) as T;
    return { ok: true, data };
  } catch {
    return REJECTED;
  }
}

// ---------------------------------------------------------------------------
// Auth (public — no session yet)
// ---------------------------------------------------------------------------

/**
 * POST /api/supplier/auth/request-link.
 *
 * The backend answers 200 {"ok":true} for EVERY outcome — known member,
 * unknown email, no account, pending member, unparseable address. This client
 * must not reintroduce the oracle the backend carefully avoids, so it reports
 * only "the request completed" vs "it didn't". A rate-limited 429 is reported
 * as `rateLimited` so the UI can say "try again shortly" — a limit that is
 * applied BEFORE any account lookup, so it is not an existence signal either.
 */
export async function requestMagicLink(
  email: string,
): Promise<{ ok: true } | { ok: false; rateLimited: boolean }> {
  try {
    const res = await fetch("/api/supplier/auth/request-link", {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
    if (res.ok) return { ok: true };
    return { ok: false, rateLimited: res.status === 429 };
  } catch {
    return { ok: false, rateLimited: false };
  }
}

/**
 * POST /api/supplier/auth/verify — exchange a magic-link token for a session.
 *
 * The response body carries a raw bearer token for API clients. THIS CLIENT
 * IGNORES IT: the browser's session is the httpOnly cookie the same response
 * sets. The body is never stored, never logged, never returned to a caller —
 * hence the `boolean` result. Every failure (unknown / expired / used /
 * pending member) is the backend's one uniform 401, so there is nothing to
 * branch on here either.
 */
export async function verifyMagicLink(token: string): Promise<boolean> {
  try {
    const res = await fetch("/api/supplier/auth/verify", {
      method: "POST",
      cache: "no-store",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    return res.ok;
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Session-scoped endpoints
// ---------------------------------------------------------------------------

/** GET /api/supplier/me — the account + the member + their capabilities. */
export function getSupplierMe(): Promise<SessionResult<SupplierMe>> {
  return sessionFetch<SupplierMe>("/me");
}

/** POST /api/supplier/auth/logout — revoke server-side and clear the cookie. */
export function logoutSupplier(): Promise<SessionResult<{ ok: boolean }>> {
  return sessionFetch<{ ok: boolean }>("/auth/logout", { method: "POST" });
}

/** GET /api/supplier/requests — the account's open RFQs. */
export function getSessionOpenRequests(): Promise<
  SessionResult<{ requests: OpenRequest[] }>
> {
  return sessionFetch<{ requests: OpenRequest[] }>("/requests");
}

/** GET /api/supplier/quotes — the account's quote history. */
export function getSessionQuoteHistory(): Promise<
  SessionResult<{ quotes: QuoteHistoryRow[] }>
> {
  return sessionFetch<{ quotes: QuoteHistoryRow[] }>("/quotes");
}

/** POST /api/supplier/quotes — submit a quote under the session identity. */
export function submitSessionQuote(
  body: PortalQuoteBody,
): Promise<SessionResult<PortalQuoteSubmitResponse>> {
  return sessionFetch<PortalQuoteSubmitResponse>("/quotes", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** GET /api/supplier/profile — the account's editable profile + demand teaser. */
export function getSessionProfile(): Promise<SessionResult<PortalProfile>> {
  return sessionFetch<PortalProfile>("/profile");
}

/** POST /api/supplier/propose-revision — a proposal, never a save. */
export function proposeSessionRevision(
  body: ProposeRevisionBody,
): Promise<SessionResult<ProposeRevisionResponse>> {
  return sessionFetch<ProposeRevisionResponse>("/propose-revision", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// ---------------------------------------------------------------------------
// Member management (T11) — the UI reflects these, the server enforces them
// ---------------------------------------------------------------------------

export function getAccountMembers(): Promise<
  SessionResult<{ members: AccountMember[] }>
> {
  return sessionFetch<{ members: AccountMember[] }>("/members", undefined, true);
}

export function inviteAccountMember(
  email: string,
  role: SupplierRole,
): Promise<SessionResult<{ ok: boolean; member: AccountMember }>> {
  return sessionFetch("/members/invite", {
    method: "POST",
    body: JSON.stringify({ email, role }),
  }, true);
}

export function changeAccountMemberRole(
  memberId: string,
  role: SupplierRole,
): Promise<SessionResult<{ ok: boolean; member: AccountMember }>> {
  return sessionFetch(`/members/${encodeURIComponent(memberId)}/role`, {
    method: "POST",
    body: JSON.stringify({ role }),
  }, true);
}

export function revokeAccountMember(
  memberId: string,
): Promise<SessionResult<{ ok: boolean; member: AccountMember }>> {
  return sessionFetch(`/members/${encodeURIComponent(memberId)}/revoke`, {
    method: "POST",
  }, true);
}
