// @vitest-environment jsdom
/**
 * Arc 3 T4 — /supplier/login.
 *
 * The falsifiable form of D5 ("no auth UI copy distinguishes a known email
 * from an unknown one") is an EQUALITY, not three "renders something" checks:
 * drive the screen through every outcome the backend can produce and assert
 * the rendered text collapses to a set of size one. A contrast case proves
 * the assertion would fail on divergent output, so it cannot pass vacuously.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LoginScreen } from "../login-screen";
import SupplierLoginPage from "../page";
import {
  headerValue,
  jsonResponse,
  spyConsole,
  consoleDump,
  storageDump,
  stubFetch,
  tooManyRequests,
} from "@/test-support/fetch-seam";

const FLAG = "NEXT_PUBLIC_SUPPLIER_SESSION_V1";

afterEach(() => {
  // vitest.setup.ts is a pre-existing shared file and is not modified by this
  // arc; it does not call unstubAllEnvs, and vitest's unstubEnvs option is off
  // by default. Without this the flag leaks into the next test in the file.
  vi.unstubAllEnvs();
});

/** Submit the form with `email` and return the whole rendered text. */
async function submitAndRead(email: string): Promise<string> {
  const user = userEvent.setup();
  const view = render(<LoginScreen />);
  await user.type(screen.getByLabelText("Work email"), email);
  await user.click(
    screen.getByRole("button", { name: "Email me a sign-in link" }),
  );
  await screen.findByText("Check your email");
  const text = view.container.textContent ?? "";
  view.unmount();
  return text;
}

// ---------------------------------------------------------------------------
// The request
// ---------------------------------------------------------------------------

describe("login screen — the request", () => {
  it("posts the email to request-link and nothing else", async () => {
    const calls = stubFetch(() => jsonResponse(200, { ok: true }));
    const user = userEvent.setup();
    render(<LoginScreen />);
    await user.type(screen.getByLabelText("Work email"), "sales@dxpe.com");
    await user.click(
      screen.getByRole("button", { name: "Email me a sign-in link" }),
    );
    await screen.findByText("Check your email");

    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("/api/supplier/auth/request-link");
    expect(calls[0].init?.method).toBe("POST");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({
      email: "sales@dxpe.com",
    });
    // No credential of any kind on a public endpoint.
    expect(headerValue(calls[0], "authorization")).toBeNull();
  });

  it("trims the address and refuses to submit an empty one", async () => {
    const calls = stubFetch(() => jsonResponse(200, { ok: true }));
    const user = userEvent.setup();
    render(<LoginScreen />);
    const submit = screen.getByRole("button", {
      name: "Email me a sign-in link",
    });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    await user.type(screen.getByLabelText("Work email"), "  sales@dxpe.com  ");
    await user.click(submit);
    await screen.findByText("Check your email");
    expect(JSON.parse(String(calls[0].init?.body)).email).toBe("sales@dxpe.com");
  });

  it("does no client-side existence or domain validation", async () => {
    // A browser-side "that isn't a supplier domain" check would be an oracle
    // built entirely in the UI. Anything non-empty is sent.
    const calls = stubFetch(() => jsonResponse(200, { ok: true }));
    const user = userEvent.setup();
    render(<LoginScreen />);
    await user.type(screen.getByLabelText("Work email"), "not-an-email");
    await user.click(
      screen.getByRole("button", { name: "Email me a sign-in link" }),
    );
    await screen.findByText("Check your email");
    expect(calls).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// D5 — the equality
// ---------------------------------------------------------------------------

describe("login screen — no enumeration oracle (D5)", () => {
  it("renders byte-identical output for known, unknown and unparseable", async () => {
    // The backend answers 200 {"ok":true} to all three; the UI must not
    // manufacture a difference the backend refused to make.
    stubFetch(() => jsonResponse(200, { ok: true }));
    const outputs = new Set([
      await submitAndRead("known@dxpe.com"),
      await submitAndRead("nobody@unknown.example"),
      await submitAndRead("garbage-not-an-email"),
    ]);
    expect(outputs.size).toBe(1);
  });

  it("never echoes the address back or says whether it was found", async () => {
    stubFetch(() => jsonResponse(200, { ok: true }));
    const text = await submitAndRead("sales@dxpe.com");
    expect(text).not.toContain("sales@dxpe.com");
    expect(text).not.toContain("dxpe.com");
    expect(text).not.toMatch(/not found|no account|unknown|isn't registered/i);
    // "check your spam" would imply a send definitely happened — an oracle
    // for anyone who then sees nothing arrive.
    expect(text).not.toMatch(/spam|junk folder/i);
    expect(text).toMatch(/if that address is on file/i);
  });

  it("the equality check would FAIL on divergent output (contrast case)", async () => {
    // Proves the Set-size-1 assertion has teeth: two genuinely different
    // renders must produce a set of size two.
    let first = true;
    stubFetch(() => {
      const res = first ? jsonResponse(200, { ok: true }) : tooManyRequests();
      first = false;
      return res;
    });
    const sent = await submitAndRead("a@dxpe.com");

    const user = userEvent.setup();
    const view = render(<LoginScreen />);
    await user.type(screen.getByLabelText("Work email"), "b@dxpe.com");
    await user.click(
      screen.getByRole("button", { name: "Email me a sign-in link" }),
    );
    await screen.findByRole("alert");
    const limited = view.container.textContent ?? "";
    expect(new Set([sent, limited]).size).toBe(2);
  });
});

// ---------------------------------------------------------------------------
// Rate limiting — a retry prompt, not a status report
// ---------------------------------------------------------------------------

describe("login screen — rate-limit feedback", () => {
  it("shows the same retry copy for 429 and for a network failure", async () => {
    const read = async (responder: () => Response) => {
      stubFetch(responder);
      const user = userEvent.setup();
      const view = render(<LoginScreen />);
      await user.type(screen.getByLabelText("Work email"), "x@dxpe.com");
      await user.click(
        screen.getByRole("button", { name: "Email me a sign-in link" }),
      );
      const text = (await screen.findByRole("alert")).textContent ?? "";
      view.unmount();
      return text;
    };
    const limited = await read(() => tooManyRequests());
    const failed = await read(() => {
      throw new Error("offline");
    });
    expect(limited).toBe(failed);
    // And it says nothing about the address.
    expect(limited).not.toMatch(/too many attempts for|that address|account/i);
  });

  it("keeps the form on screen so the attempt can be retried", async () => {
    stubFetch(() => tooManyRequests());
    const user = userEvent.setup();
    render(<LoginScreen />);
    await user.type(screen.getByLabelText("Work email"), "x@dxpe.com");
    await user.click(
      screen.getByRole("button", { name: "Email me a sign-in link" }),
    );
    await screen.findByRole("alert");
    const field = screen.getByLabelText("Work email") as HTMLInputElement;
    expect(field.value).toBe("x@dxpe.com");
  });
});

// ---------------------------------------------------------------------------
// Hygiene
// ---------------------------------------------------------------------------

describe("login screen — hygiene", () => {
  it("persists and logs nothing across a full submit", async () => {
    const spies = spyConsole();
    stubFetch(() => jsonResponse(200, { ok: true }));
    const user = userEvent.setup();
    render(<LoginScreen />);
    await user.type(screen.getByLabelText("Work email"), "sales@dxpe.com");
    await user.click(
      screen.getByRole("button", { name: "Email me a sign-in link" }),
    );
    await screen.findByText("Check your email");

    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
    expect(storageDump(window.localStorage)).toBe("");
    expect(document.cookie).toBe("");
    expect(consoleDump(spies)).toBe("");
  });

  it("calls only /api/supplier/* — never an admin or portal path", async () => {
    const calls = stubFetch(() => jsonResponse(200, { ok: true }));
    const user = userEvent.setup();
    render(<LoginScreen />);
    await user.type(screen.getByLabelText("Work email"), "sales@dxpe.com");
    await user.click(
      screen.getByRole("button", { name: "Email me a sign-in link" }),
    );
    await screen.findByText("Check your email");
    expect(calls.every((c) => c.url.startsWith("/api/supplier/"))).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// The route's flag gate
// ---------------------------------------------------------------------------

describe("/supplier/login route — flag gating", () => {
  it("renders NOTHING and makes no request when the flag is off", () => {
    const calls = stubFetch(() => jsonResponse(200, { ok: true }));
    const { container } = render(<SupplierLoginPage />);
    expect(container.textContent).toBe("");
    expect(container.querySelector("form")).toBeNull();
    expect(calls).toHaveLength(0);
  });

  it("renders the login form when the flag is on", () => {
    vi.stubEnv(FLAG, "1");
    render(<SupplierLoginPage />);
    expect(screen.getByLabelText("Work email")).toBeTruthy();
  });

  it("treats an unrecognised flag value as off", () => {
    vi.stubEnv(FLAG, "maybe");
    const { container } = render(<SupplierLoginPage />);
    expect(container.textContent).toBe("");
  });
});
