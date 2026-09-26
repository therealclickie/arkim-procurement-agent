/**
 * Buyer identity API (arc 6) — login, "who am I", team and approval policy.
 *
 * The session is the httpOnly `gofer_buyer_session` cookie. This module never
 * reads, writes or sees it: `credentials: "include"` lets the browser carry it,
 * and that is the whole of the auth mechanism. No token is ever returned to
 * script (the backend's verify deliberately answers with no token in the body).
 *
 * Every call goes through the same-origin `/api/buyer` path (the Next rewrite
 * to FastAPI), so the cookie is first-party.
 */

export type BuyerCapability =
  | "raise_request"
  | "view_company"
  | "select_and_order"
  | "approve_within_limit"
  | "second_approval"
  | "override_limit"
  | "set_approval_limit"
  | "toggle_admin_override"
  | "manage_members";

export type BuyerRole = "REQUESTER" | "BUYER" | "APPROVER" | "ADMIN";

/** Display order and copy for the four roles (D2). */
export const BUYER_ROLES: { value: BuyerRole; label: string }[] = [
  { value: "REQUESTER", label: "Requester" },
  { value: "BUYER", label: "Buyer" },
  { value: "APPROVER", label: "Approver" },
  { value: "ADMIN", label: "Admin" },
];

export function roleLabel(role: string | undefined | null): string {
  return BUYER_ROLES.find((r) => r.value === role)?.label ?? (role ?? "");
}

export interface BuyerCompany {
  id: string;
  name: string;
  facility_ids: string[];
}

export interface BuyerMember {
  id: string;
  email: string;
  role: BuyerRole;
  status: "ACTIVE" | "REVOKED";
  created_at: string;
  permissions?: BuyerCapability[];
}

export interface BuyerMe {
  company: BuyerCompany;
  member: BuyerMember & { permissions: BuyerCapability[] };
}

export interface ApprovalPolicy {
  auto_approval_limit: number;
  allow_admin_override: boolean;
  currency: string;
}

export type BuyerResult<T> =
  | { ok: true; data: T }
  | { ok: false; unauthorized: true }
  | { ok: false; rejected: true; status?: number; detail?: string };

async function buyerFetch<T>(path: string, init?: RequestInit): Promise<BuyerResult<T>> {
  try {
    const res = await fetch(`/api/buyer${path}`, {
      cache: "no-store",
      credentials: "include",
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
      ...init,
    });
    if (res.status === 401) return { ok: false, unauthorized: true };
    if (!res.ok) {
      let detail: string | undefined;
      try {
        const body = (await res.json()) as { detail?: unknown };
        detail = typeof body.detail === "string" ? body.detail : undefined;
      } catch {
        detail = undefined;
      }
      return { ok: false, rejected: true, status: res.status, detail };
    }
    return { ok: true, data: (await res.json()) as T };
  } catch {
    return { ok: false, rejected: true };
  }
}

// ---------------------------------------------------------------------------
// Auth (no session yet)
// ---------------------------------------------------------------------------

/** POST /api/buyer/auth/request-link. The backend answers the same 200 for
 *  every outcome (known, unknown, revoked, rate-limited), so this reports only
 *  whether the request itself completed. */
export async function requestBuyerLink(email: string): Promise<{ ok: boolean }> {
  const r = await buyerFetch<{ ok: boolean }>("/auth/request-link", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
  return { ok: r.ok };
}

/** POST /api/buyer/auth/verify. Every failure is one outcome: false. */
export async function verifyBuyerLink(token: string): Promise<boolean> {
  const r = await buyerFetch<{ ok: boolean }>("/auth/verify", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
  return r.ok;
}

export function getBuyerMe(): Promise<BuyerResult<BuyerMe>> {
  return buyerFetch<BuyerMe>("/me");
}

export async function logoutBuyer(): Promise<void> {
  await buyerFetch<{ ok: boolean }>("/auth/logout", { method: "POST" });
}

// ---------------------------------------------------------------------------
// Team (Admin)
// ---------------------------------------------------------------------------

export function listBuyerMembers(): Promise<BuyerResult<{ count: number; members: BuyerMember[] }>> {
  return buyerFetch("/members");
}

export function inviteBuyerMember(email: string, role?: BuyerRole):
    Promise<BuyerResult<{ ok: boolean; member: BuyerMember }>> {
  return buyerFetch("/members", {
    method: "POST",
    body: JSON.stringify(role ? { email, role } : { email }),
  });
}

export function changeBuyerMemberRole(memberId: string, role: BuyerRole):
    Promise<BuyerResult<{ ok: boolean; member: BuyerMember }>> {
  return buyerFetch(`/members/${encodeURIComponent(memberId)}/role`, {
    method: "POST",
    body: JSON.stringify({ role }),
  });
}

export function revokeBuyerMember(memberId: string):
    Promise<BuyerResult<{ ok: boolean; member: BuyerMember }>> {
  return buyerFetch(`/members/${encodeURIComponent(memberId)}/revoke`, { method: "POST" });
}

// ---------------------------------------------------------------------------
// Approval policy (Admin) — stored and audited; enforced in a later arc
// ---------------------------------------------------------------------------

export function getBuyerSettings():
    Promise<BuyerResult<{ company_id: string; approval_policy: ApprovalPolicy }>> {
  return buyerFetch("/settings");
}

export function saveApprovalPolicy(change: {
  auto_approval_limit?: number;
  allow_admin_override?: boolean;
}): Promise<BuyerResult<{ company_id: string; approval_policy: ApprovalPolicy }>> {
  return buyerFetch("/settings/approval-policy", {
    method: "PUT",
    body: JSON.stringify(change),
  });
}
