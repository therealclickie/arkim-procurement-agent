"use client";

/**
 * Shared chrome for the authenticated supplier surfaces.
 *
 * Reuses the PUBLIC portal's visual system (`.portal-surface` and its tokens)
 * rather than introducing a second one: a supplier who claimed their profile
 * through a link and a supplier who signed in are the same person looking at
 * the same company, and two palettes would say otherwise.
 *
 * Deliberately NOT here: any auth gate. `/supplier/login` and
 * `/supplier/verify` use this shell and must stay reachable with no session,
 * so the guard is a separate component (see session-guard.tsx, T10) applied
 * by the pages that need it.
 */

import { BRAND_NAME } from "@/lib/brand";

/**
 * The page frame: brand header, main column, footer. `aside` is the
 * right-hand header slot — the supplier's identity once there is one.
 */
export function SupplierSurface({
  aside,
  children,
}: {
  aside?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="portal-surface">
      <header className="portal-header">
        <span className="portal-brand">{BRAND_NAME}</span>
        {aside ? <span className="portal-supplier">{aside}</span> : null}
      </header>
      <main className="portal-main">{children}</main>
      <footer className="portal-footer">
        {BRAND_NAME} never asks for payment or credentials over chat.
      </footer>
    </div>
  );
}

/**
 * A centred message card — used for every terminal state on the auth
 * surfaces (link sent, link rejected, signed out).
 *
 * The `title`/`body` are passed in rather than derived from a status code on
 * purpose: the sent-confirmation and the rejection each have exactly ONE
 * wording, and a caller that wants to vary it has to write the variant
 * visibly rather than acquire it by branching.
 */
export function SupplierNotice({
  title,
  body,
  children,
}: {
  title: string;
  body: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <section className="supplier-notice" aria-labelledby="supplier-notice-h">
      <h1 className="supplier-notice-title" id="supplier-notice-h">
        {title}
      </h1>
      <div className="supplier-notice-body">{body}</div>
      {children}
    </section>
  );
}
