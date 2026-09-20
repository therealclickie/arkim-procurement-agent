"use client";

/**
 * The AUTH-MODE SEAM (arc 3 D3): one set of portal components, two ways of
 * being authenticated.
 *
 * WHY AN OBJECT AND NOT A FLAG. Session mode is not "the same URL plus a
 * credential" — it is a different URL:
 *
 *     open requests   /api/portal/{token}/open-requests  →  /api/supplier/requests
 *     quote history   /api/portal/{token}/quotes         →  /api/supplier/quotes
 *     submit quote    /api/portal/{token}/quotes         →  /api/supplier/quotes
 *     profile         /api/portal/{token}/profile        →  /api/supplier/profile
 *     revision        /api/portal/{token}/propose-revision → /api/supplier/propose-revision
 *
 * So a boolean threaded into the fetch primitive could not express it. A mode
 * object resolves a LOGICAL operation ("get my open requests") to a concrete
 * request, and the components call the operation. They never learn which door
 * they came in by.
 *
 * WHY A CONTEXT WITH A TOKEN-MODE FALLBACK. `<OpenRequests token={t} />` with
 * no provider is exactly how arc 1's unmodifiable characterisation tests
 * render it. Making the mode a required prop would break them. So the mode is
 * a context whose absence means token mode — session surfaces wrap their tree
 * in a provider, the claim page does not, and the claim page's behaviour is
 * byte-identical to before this arc.
 *
 * WHAT DELIBERATELY DOES NOT CHANGE. `portalFetch` and `quoteFetch` are
 * untouched — same headers, same `cache: "no-store"`, still no credentials,
 * still no Authorization. Token-mode requests are identical to today's,
 * which is what arc 1's network-reach assertions pin.
 */

import { createContext, useContext, useMemo } from "react";
import {
  getOpenRequests,
  getPortalProfile,
  getQuoteHistory,
  proposeRevision,
  submitPortalQuote,
  type OpenRequest,
  type PortalProfile,
  type PortalQuoteBody,
  type PortalQuoteSubmitResponse,
  type PortalResult,
  type ProposeRevisionBody,
  type ProposeRevisionResponse,
  type QuoteHistoryRow,
} from "@/lib/portal-api";
import {
  getSessionOpenRequests,
  getSessionProfile,
  getSessionQuoteHistory,
  proposeSessionRevision,
  submitSessionQuote,
  type SessionResult,
} from "@/lib/supplier-api";

// ---------------------------------------------------------------------------
// The interface both modes satisfy
// ---------------------------------------------------------------------------

export interface SupplierMode {
  /** For diagnostics and for the few places that legitimately differ (e.g. a
   *  session page owning its own empty-state copy). Never for auth. */
  readonly kind: "token" | "session";
  getProfile(): Promise<PortalResult<PortalProfile>>;
  proposeRevision(
    body: ProposeRevisionBody,
  ): Promise<PortalResult<ProposeRevisionResponse>>;
  getOpenRequests(): Promise<PortalResult<{ requests: OpenRequest[] }>>;
  getQuoteHistory(): Promise<PortalResult<{ quotes: QuoteHistoryRow[] }>>;
  submitQuote(
    body: PortalQuoteBody,
  ): Promise<PortalResult<PortalQuoteSubmitResponse>>;
}

// ---------------------------------------------------------------------------
// Token mode — a pass-through. Every call is the existing portal-api function
// with the existing argument, so the emitted request is unchanged.
// ---------------------------------------------------------------------------

export function tokenMode(token: string): SupplierMode {
  return {
    kind: "token",
    getProfile: () => getPortalProfile(token),
    proposeRevision: (body) => proposeRevision(token, body),
    getOpenRequests: () => getOpenRequests(token),
    getQuoteHistory: () => getQuoteHistory(token),
    submitQuote: (body) => submitPortalQuote(token, body),
  };
}

// ---------------------------------------------------------------------------
// Session mode
// ---------------------------------------------------------------------------

export interface SessionModeOptions {
  /**
   * Called when any session call comes back 401. The mode itself does not
   * navigate — a data client that redirects is a client that cannot be
   * tested or reused. The page decides (T10's guard sends the user to login).
   */
  onUnauthorized?: () => void;
}

/**
 * Collapse a `SessionResult` to the `PortalResult` the components expect,
 * notifying the caller on 401 first. The 401 signal is deliberately NOT
 * widened into the component contract: to a component, "my session expired"
 * and "that failed" both mean "render nothing / show the soft error", and the
 * navigation is the page's job.
 */
function toPortalResult<T>(
  result: SessionResult<T>,
  onUnauthorized?: () => void,
): PortalResult<T> {
  if (result.ok) return { ok: true, data: result.data };
  if ("unauthorized" in result) onUnauthorized?.();
  return { ok: false, rejected: true };
}

export function sessionMode(opts: SessionModeOptions = {}): SupplierMode {
  const { onUnauthorized } = opts;
  const map = <T,>(p: Promise<SessionResult<T>>): Promise<PortalResult<T>> =>
    p.then((r) => toPortalResult(r, onUnauthorized));
  return {
    kind: "session",
    getProfile: () => map(getSessionProfile()),
    proposeRevision: (body) => map(proposeSessionRevision(body)),
    getOpenRequests: () => map(getSessionOpenRequests()),
    getQuoteHistory: () => map(getSessionQuoteHistory()),
    submitQuote: (body) => map(submitSessionQuote(body)),
  };
}

// ---------------------------------------------------------------------------
// The context
// ---------------------------------------------------------------------------

const SupplierModeContext = createContext<SupplierMode | null>(null);

export function SupplierModeProvider({
  mode,
  children,
}: {
  mode: SupplierMode;
  children: React.ReactNode;
}) {
  return (
    <SupplierModeContext.Provider value={mode}>
      {children}
    </SupplierModeContext.Provider>
  );
}

/**
 * The mode a component should use: the provided one, or token mode built from
 * the `token` prop it already takes. Outside any provider with a token, this
 * is exactly today's behaviour — which is what keeps arc 1's tests green
 * without editing them.
 */
export function useSupplierMode(token?: string): SupplierMode {
  const provided = useContext(SupplierModeContext);
  return useMemo(
    () => provided ?? tokenMode(token ?? ""),
    [provided, token],
  );
}
