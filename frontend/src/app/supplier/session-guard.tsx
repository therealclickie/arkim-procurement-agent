"use client";

/**
 * SessionGuard — the client-side gate on the authenticated supplier routes.
 *
 * WHAT THIS IS NOT. It is not a security boundary. The session is an httpOnly
 * cookie this code cannot read, so the only way to know whether one exists is
 * to ask the server, and the only thing being protected here is the user's
 * experience: without a session there is no data to render, so send them to
 * login rather than show an authenticated shell around nothing. Every piece
 * of actual enforcement is server-side — the routes 401 regardless of what
 * this component decides.
 *
 * Which is why it lives in the PAGES that need it and not in
 * `supplier/layout.tsx`: `/supplier/login` and `/supplier/verify` are
 * siblings under the same layout and must stay reachable with no session. A
 * guard in the layout would lock the door and leave the key inside.
 *
 * It also supplies the session-mode object to everything beneath it, so the
 * shared portal components fetch through the session door without knowing it.
 */

import { useEffect, useMemo } from "react";
import { useRouter } from "next/navigation";
import { SupplierModeProvider, sessionMode } from "@/lib/supplier-mode";
import type { SupplierSession } from "@/lib/use-supplier-session";
import { useSupplierSession } from "@/lib/use-supplier-session";
import { GoferLoader } from "@/components/ui/gofer-loader";
import { SupplierSurface } from "./supplier-chrome";

export const LOGIN_PATH = "/supplier/login";

export function SessionGuard({
  children,
}: {
  children: (session: SupplierSession) => React.ReactNode;
}) {
  const router = useRouter();
  const session = useSupplierSession();
  const { loading, unauthorized } = session;

  useEffect(() => {
    if (!loading && unauthorized) router.replace(LOGIN_PATH);
  }, [loading, unauthorized, router]);

  // A 401 on any later call also lands here: sessionMode reports it, and the
  // refresh confirms the session is gone, which flips `unauthorized` and the
  // effect above navigates. The mode never navigates on its own — a data
  // client that redirects is one that cannot be tested or reused.
  const mode = useMemo(
    () => sessionMode({ onUnauthorized: () => void session.refresh() }),
    [session.refresh], // eslint-disable-line react-hooks/exhaustive-deps
  );

  if (loading) {
    return (
      <SupplierSurface>
        <div className="portal-loading">
          <GoferLoader size={96} aria-label="Loading your account" />
          <p className="portal-loading-text">Loading your account…</p>
        </div>
      </SupplierSurface>
    );
  }

  // Render nothing while the redirect happens — never a half-authenticated
  // page, and never a "you are signed out" screen that competes with login.
  if (unauthorized) return null;

  return (
    <SupplierModeProvider mode={mode}>{children(session)}</SupplierModeProvider>
  );
}
