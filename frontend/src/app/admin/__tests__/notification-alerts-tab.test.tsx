// @vitest-environment jsdom
/**
 * Arc 4 T12 — the Notification Alerts tab on /admin.
 *
 * The internal admin surface has no frontend flag by design: it is a
 * token-gated debug page, and with the backend's NOTIFICATIONS_V1 off the
 * endpoint 404s and the tab shows the same error panel any other absent admin
 * surface does. So what is worth pinning here is the operator's loop:
 *
 *   an open alert is visible with enough to act on
 *   → Acknowledge POSTs to the right URL with the bearer token in a HEADER
 *   → the row leaves the open list
 *
 * and the auth posture the rest of the admin page already keeps: the token
 * never appears in a URL.
 */
import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AdminInspectorPage from "../page";
import { stubFetch, jsonResponse, type FetchCall } from "@/test-support/fetch-seam";

const ADMIN_TOKEN = "admin_tok_secret_arc4";
const TOKEN_KEY = "arkim_admin_token";
const ALERTS_URL = "/api/admin/notification-alerts";

const ESCALATION = {
  id: "alert_esc_1",
  kind: "RFQ_ESCALATION",
  status: "open",
  created_at: "2026-09-21T08:00:00Z",
  run_id: "run_42",
  supplier_domain: "dxpe.com",
  email: "sales@dxpe.com",
  acknowledged_at: null,
  acknowledged_by: null,
};

const SUPPRESSION = {
  id: "alert_sup_1",
  kind: "EMAIL_SUPPRESSED",
  status: "open",
  created_at: "2026-09-21T07:00:00Z",
  run_id: null,
  supplier_domain: "dxpe.com",
  email: "bounced@dxpe.com",
  acknowledged_at: null,
  acknowledged_by: null,
};

/** The admin backend, with the alert list mutable so an acknowledge is
 *  observable as a change in what the next GET returns. */
function setupAdmin({
  alerts = [ESCALATION, SUPPRESSION],
  alertsStatus = 200,
  ackStatus = 200,
}: {
  alerts?: Record<string, unknown>[];
  alertsStatus?: number;
  ackStatus?: number;
} = {}) {
  window.localStorage.clear();
  window.localStorage.setItem(TOKEN_KEY, ADMIN_TOKEN);
  let open = [...alerts];
  const calls = stubFetch((call) => {
    const method = call.init?.method ?? "GET";
    if (call.url === "/api/admin/runs") return jsonResponse(200, { runs: [], count: 0 });
    if (call.url === ALERTS_URL && method === "GET") {
      if (alertsStatus !== 200) return jsonResponse(alertsStatus, { detail: "Not Found" });
      return jsonResponse(200, { count: open.length, alerts: open });
    }
    const ack = /^\/api\/admin\/notification-alerts\/(.+)\/acknowledge$/.exec(call.url);
    if (ack && method === "POST") {
      if (ackStatus !== 200) return jsonResponse(ackStatus, { detail: "Alert not found" });
      open = open.filter((a) => a.id !== ack[1]);
      return jsonResponse(200, { ok: true, alert: { id: ack[1], status: "acknowledged" } });
    }
    return jsonResponse(404, { detail: "Not Found" });
  });
  const user = userEvent.setup();
  render(<AdminInspectorPage />);
  return { calls, user };
}

async function openAlertsTab(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByText("Pipeline Inspector");
  await user.click(screen.getByRole("button", { name: "Notification Alerts" }));
}

function bearer(call: FetchCall): string | undefined {
  const headers = (call.init?.headers ?? {}) as Record<string, string>;
  return headers.Authorization ?? headers.authorization;
}

describe("admin notification alerts — listing", () => {
  it("lists the open alerts with what an operator needs to act", async () => {
    const { user } = setupAdmin();
    await openAlertsTab(user);
    await screen.findByText("RFQ_ESCALATION");
    expect(screen.getByText("run_42")).toBeTruthy();
    expect(screen.getAllByText("dxpe.com").length).toBeGreaterThan(0);
    expect(screen.getByText("sales@dxpe.com")).toBeTruthy();
    expect(screen.getByText("EMAIL_SUPPRESSED")).toBeTruthy();
  });

  it("sends the admin token as a header and never in the URL", async () => {
    const { calls, user } = setupAdmin();
    await openAlertsTab(user);
    await screen.findByText("RFQ_ESCALATION");
    const get = calls.find((c) => c.url === ALERTS_URL);
    expect(bearer(get!)).toBe(`Bearer ${ADMIN_TOKEN}`);
    expect(calls.every((c) => !c.url.includes(ADMIN_TOKEN))).toBe(true);
  });

  it("shows the ordinary error panel when the backend flag is off", async () => {
    const { user } = setupAdmin({ alertsStatus: 404 });
    await openAlertsTab(user);
    await screen.findByText(/HTTP 404/);
    expect(screen.queryByRole("button", { name: "Acknowledge" })).toBeNull();
  });
});

describe("admin notification alerts — acknowledge", () => {
  it("POSTs the acknowledge and drops the row from the open list", async () => {
    const { calls, user } = setupAdmin();
    await openAlertsTab(user);
    await screen.findByText("RFQ_ESCALATION");

    const rows = screen.getAllByRole("button", { name: "Acknowledge" });
    expect(rows).toHaveLength(2);
    await user.click(rows[0]);

    await waitFor(() => expect(screen.queryByText("RFQ_ESCALATION")).toBeNull());
    const post = calls.find(
      (c) => c.url === `${ALERTS_URL}/${ESCALATION.id}/acknowledge`,
    );
    expect(post?.init?.method).toBe("POST");
    expect(bearer(post!)).toBe(`Bearer ${ADMIN_TOKEN}`);
    // The other alert is a different problem and stays open.
    expect(screen.getByText("EMAIL_SUPPRESSED")).toBeTruthy();
  });

  it("surfaces a rejected acknowledge instead of pretending it worked", async () => {
    const { user } = setupAdmin({ ackStatus: 409 });
    await openAlertsTab(user);
    await screen.findByText("RFQ_ESCALATION");
    await user.click(screen.getAllByRole("button", { name: "Acknowledge" })[0]);
    await screen.findByText(/HTTP 409/);
  });
});
