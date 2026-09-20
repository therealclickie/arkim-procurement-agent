/**
 * Frontend feature flags.
 *
 * There was no frontend flag convention before arc 3 (the gate looked: the
 * only NEXT_PUBLIC_* in the tree was NEXT_PUBLIC_API_URL, which is config).
 * Flagged features were gated IMPLICITLY, via the backend — when a flag is off
 * its endpoints 404 and the section renders nothing. That is the right shape
 * for a *section* inside an existing page, and it is deliberately kept.
 *
 * It cannot gate a *route*, though: /supplier/login has no backend call to
 * 404 on before first paint, so "the route is inert when off" has to be a
 * property the page itself asserts. Hence this module.
 *
 * TWO RULES, both load-bearing:
 *
 *  1. Read `process.env.NEXT_PUBLIC_*` as a LITERAL member expression. Next's
 *     SWC substitutes the value at build time only for that exact syntax — a
 *     computed key (`process.env[name]`) is never inlined and the flag would
 *     read as undefined in a production build.
 *  2. Default OFF. An unset or unrecognised value is off, never on.
 */

/** Truthy spellings, matching the backend's `_env_truthy`. */
function isOn(value: string | undefined): boolean {
  if (!value) return false;
  return ["1", "true", "yes", "on"].includes(value.trim().toLowerCase());
}

/**
 * NEXT_PUBLIC_SUPPLIER_SESSION_V1 — arc 3's supplier browser-session surface
 * (/supplier/login, /verify, /requests, /profile, /members) and the claim →
 * account bridge CTA. Off ⇒ none of them render anything.
 *
 * Paired with, and independent of, the backend's SUPPLIER_ACCOUNTS_V1: the
 * backend flag makes the API absent, this one makes the UI absent. Either off
 * is enough for the surface to be unreachable.
 */
export function supplierSessionEnabled(): boolean {
  return isOn(process.env.NEXT_PUBLIC_SUPPLIER_SESSION_V1);
}
