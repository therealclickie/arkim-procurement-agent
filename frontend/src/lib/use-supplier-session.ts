"use client";

/**
 * useSupplierSession — "who am I", for the authenticated supplier surfaces.
 *
 * The session itself is an httpOnly cookie this hook cannot see (D1). The
 * only way to know whether one exists is to ask the server, so the hook's
 * whole job is a `GET /api/supplier/me` and the three states it can produce:
 *
 *   loading        the answer is not back yet — render nothing decisive
 *   authenticated  account + member + permissions are known
 *   unauthorized   there is no usable session; the caller sends them to login
 *
 * `permissions` comes from the server's capability matrix and is used ONLY to
 * decide what to SHOW (D6). Every gated action is re-checked server-side, so a
 * user who edits this array in a debugger gains a visible button and a 403.
 */

import { useCallback, useEffect, useState } from "react";
import {
  getSupplierMe,
  logoutSupplier,
  type SupplierAccount,
  type SupplierCapability,
  type SupplierMember,
} from "@/lib/supplier-api";

export interface SupplierSession {
  account: SupplierAccount | null;
  member: SupplierMember | null;
  permissions: SupplierCapability[];
  loading: boolean;
  /** True once we know there is no usable session (a 401 or a failed load). */
  unauthorized: boolean;
  /** Does the signed-in member hold `capability`? False while loading. */
  can: (capability: SupplierCapability) => boolean;
  /** Re-read `me` (after a role change, say). */
  refresh: () => Promise<void>;
  /** Server logout + local clear. Resolves once the state is cleared. */
  logout: () => Promise<void>;
}

export function useSupplierSession(): SupplierSession {
  const [account, setAccount] = useState<SupplierAccount | null>(null);
  const [member, setMember] = useState<SupplierMember | null>(null);
  const [loading, setLoading] = useState(true);
  const [unauthorized, setUnauthorized] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    const res = await getSupplierMe();
    if (res.ok) {
      setAccount(res.data.account);
      setMember(res.data.member);
      setUnauthorized(false);
    } else {
      // A 401 and a transient failure are treated alike on purpose: in both
      // cases we do not have an identity, and pretending otherwise would mean
      // rendering an authenticated shell around nothing.
      setAccount(null);
      setMember(null);
      setUnauthorized(true);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const logout = useCallback(async () => {
    await logoutSupplier();
    // Clear regardless of the call's outcome. The cookie is httpOnly, so the
    // client cannot verify it is gone; holding stale identity on screen after
    // the user asked to leave is the worse failure.
    setAccount(null);
    setMember(null);
    setUnauthorized(true);
    setLoading(false);
  }, []);

  const permissions = member?.permissions ?? [];
  const can = useCallback(
    (capability: SupplierCapability) =>
      (member?.permissions ?? []).includes(capability),
    [member],
  );

  return { account, member, permissions, loading, unauthorized, can,
           refresh: load, logout };
}
