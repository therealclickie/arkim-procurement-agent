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
