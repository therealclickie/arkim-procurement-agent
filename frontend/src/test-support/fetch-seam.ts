/**
 * The test floor's single network seam (gate finding I3): every fetch on the
 * portal / quote / admin surfaces flows to GLOBAL fetch, so stubbing
 * `globalThis.fetch` is the one level at which the real request URLs and
 * headers are observable — exactly what the T3 security tests must assert.
 * Module-mocking the lib/portal-api / lib/quote-api wrappers would hide those
 * facts. vi.unstubAllGlobals() in vitest.setup.ts clears the stub per test.
 */
import { vi } from "vitest";

export interface FetchCall {
  url: string;
  init?: RequestInit;
}

export type FetchResponder = (call: FetchCall) => Response | Promise<Response>;

export function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** The uniform backend failure every public endpoint gives an unknown token. */
export const notFound = (): Response => jsonResponse(404, { detail: "Not Found" });

/**
 * Install a fetch stub that records every call (url + init) and delegates to
 * the responder. Returns the live call log for URL/header assertions.
 */
export function stubFetch(responder: FetchResponder): FetchCall[] {
  const calls: FetchCall[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const call: FetchCall = { url: String(input), init };
      calls.push(call);
      return responder(call);
    }),
  );
  return calls;
}

/** A fetch that never settles — for asserting the synchronous loading state. */
export function neverResolves(): Response {
  return new Promise<Response>(() => {}) as unknown as Response;
}

/** True when the recorded call was a POST to a URL containing `path`. */
export function isPostTo(call: FetchCall, path: string): boolean {
  return (call.init?.method ?? "GET") === "POST" && call.url.includes(path);
}

/** The uniform backend rejection for a missing / expired / revoked session
 *  (arc 2's one 401 body, extended to the cookie in arc 3 T1). */
export const unauthorized = (): Response =>
  jsonResponse(401, { detail: "Invalid or expired session" });

/** The rate-limited response the auth endpoints return over cap. */
export const tooManyRequests = (): Response =>
  jsonResponse(429, { detail: "Too many requests." });

/**
 * Every value a surface could have persisted, concatenated for a substring
 * sweep. Arc 1 declared an equivalent inside its security test file, which
 * this arc may not modify (gate finding F3) — so the shared copy lives here
 * and the duplication is forced, not careless.
 */
export function storageDump(store: Storage): string {
  const parts: string[] = [];
  for (let i = 0; i < store.length; i++) {
    const key = store.key(i);
    if (key !== null) parts.push(key, store.getItem(key) ?? "");
  }
  return parts.join("\n");
}

/** Silence and capture every console channel for the duration of a test. */
export function spyConsole() {
  return (["log", "info", "warn", "error", "debug"] as const).map((m) =>
    vi.spyOn(console, m).mockImplementation(() => {}),
  );
}

/** Everything written to the spied console channels, as one string. */
export function consoleDump(spies: ReturnType<typeof spyConsole>): string {
  return spies
    .flatMap((s) => s.mock.calls)
    .map((args) =>
      args.map((a) => (typeof a === "string" ? a : JSON.stringify(a))).join(" "),
    )
    .join("\n");
}

/**
 * Read a header off a recorded call THROUGH `Headers`, so an absence
 * assertion cannot pass vacuously because the client happened to pass a
 * `Headers` instance rather than a plain record. Case-insensitive.
 */
export function headerValue(call: FetchCall, name: string): string | null {
  return new Headers(call.init?.headers).get(name);
}
