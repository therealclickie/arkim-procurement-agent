"""S4 — Supplier silence and alert discipline.

One RFQ to three suppliers on Friday 15:00 Pacific (2026-09-25 22:00 UTC).
A views and replies. B ignores. C's address hard-bounces (injected via
notifications.apply_delivery_event — the STORE-LEVEL path; this BYPASSES SNS
webhook signature verification, which is covered by unit tests, not here).

Scheduler driven with explicit `now` (never wall clock) through Tuesday.
Expected: zero weekend sends; ONE consolidated Monday business-hours reminder
for B; ONE per-account QUEUE escalation for B; C suppressed with ACTION_NOW
(sole contact) alert; nothing dropped.

Run:  uv run python eval/e2e/s4_silence_and_alerts.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness

harness.install(live=True)
harness.isolate_stores(fresh=False)
client, api_server = harness.make_client()
provider = harness.fake_provider()

from utils import business_hours, notifications, supplier_accounts, supplier_registry
from utils import notifications_store as ns

PT = "America/Los_Angeles"
FRIDAY_3PM = datetime(2026, 9, 25, 22, 0, tzinfo=timezone.utc)   # Fri 15:00 PT
SAT = datetime(2026, 9, 26, 17, 0, tzinfo=timezone.utc)          # Sat 10:00 PT
SUN = datetime(2026, 9, 27, 21, 0, tzinfo=timezone.utc)          # Sun 14:00 PT
MON_9 = datetime(2026, 9, 28, 16, 0, tzinfo=timezone.utc)        # Mon 09:00 PT
MON_11 = datetime(2026, 9, 28, 18, 0, tzinfo=timezone.utc)       # Mon 11:00 PT
MON_3PM = datetime(2026, 9, 28, 22, 0, tzinfo=timezone.utc)      # Mon 15:00 PT
TUE_9 = datetime(2026, 9, 29, 16, 0, tzinfo=timezone.utc)        # Tue 09:00 PT

A, B, C = ("supplier-a.example.com", "supplier-b.example.com",
           "supplier-c.example.com")
STEPS: list[dict] = []


def record(step, expected, observed, verdict, evidence):
    STEPS.append({"step": step, "expected": expected, "observed": observed,
                  "verdict": verdict, "evidence": evidence})
    print(f"\n[{verdict}] {step}\n  observed: {observed[:450]}")


def s4_mails():
    return [m for m in provider.outbox
            if any(dom in t for t in m.get("to", []) for dom in (A, B, C))]


def release(dom, acct, at):
    row_id = supplier_registry.record_sent_message(
        run_id="s4-run", supplier_domain=dom, vendor_name=dom.split(".")[0],
        to=[f"sales@{dom}"], cc=[], subject="Quote request — Goulds seal",
        body="body", status="sent", part_key="goulds|s4", with_history=True)
    notifications._notify_rfq_new(
        {"run_id": "s4-run", "supplier_domain": dom, "sent_message_id": row_id,
         "manufacturer": "Goulds", "part_number": "3196-seal", "quantity": 1},
        acct, now=FRIDAY_3PM if at is None else at)
    return row_id


accounts = {d: supplier_accounts.get_account_by_domain(d) for d in (A, B, C)}
assert all(accounts.values()), f"phase-0 seed missing: {accounts}"

# ---------------------------------------------------------------------------
# Friday 15:00 PT — release the RFQ to all three
# ---------------------------------------------------------------------------
base = len(provider.outbox)
rows = {d: release(d, accounts[d], FRIDAY_3PM) for d in (A, B, C)}
notifications.run_coalesced_sends(now=FRIDAY_3PM + timedelta(minutes=15))
friday_mails = s4_mails()[len([m for m in provider.outbox[:base]]):] if base else s4_mails()
ev = harness.save_evidence("s4_step1_friday_release.json",
                           {"rows": rows,
                            "mails": [{"to": m["to"], "subject": m["subject"]}
                                      for m in friday_mails]})
record("1. Friday 15:00 PT release",
       "3 RFQ_NEW notification mails (one per owner), sent at notify time",
       f"mails to {sorted(t for m in friday_mails for t in m['to'])}",
       "PASS" if len(friday_mails) == 3 else "BREAK", ev)

# A views (and replies -> request resolved). B silent. C will bounce.
ns.record_rfq_view(run_id="s4-run", supplier_domain=A,
                   member_id=None, is_test=True)
supplier_registry.update_sent_message_status(rows[A], "replied")

# C: hard bounce via the STORE-LEVEL event path (bypasses webhook signature
# verification — deliberate, recorded; the webhook path has unit coverage).
c_notif = [n for n in ns.list_notifications(kind=ns.KIND_RFQ_NEW)
           if n.get("supplier_domain") == C]
pmid = c_notif[0].get("provider_message_id") if c_notif else None
if not pmid:
    for m in provider.outbox:
        if any(C in t for t in m.get("to", [])):
            pmid = m.get("provider_message_id")
applied = notifications.apply_delivery_event({
    "event_type": "Bounce", "provider_message_id": pmid,
    "recipients": [f"owner@{C}"], "bounce_type": "Permanent",
    "bounce_subtype": "General", "raw": {"eval": "s4 store-level injection"}})
suppressed = ns.email_suppressed(f"owner@{C}") if hasattr(ns, "email_suppressed") else None
bounce_alerts = ns.list_alerts(kind=ns.ALERT_EMAIL_SUPPRESSED, status=None)
ev = harness.save_evidence("s4_step2_setup.json",
                           {"a_viewed": True, "a_replied": True,
                            "c_pmid": pmid, "bounce_applied": applied,
                            "c_suppressed": suppressed,
                            "bounce_alerts": bounce_alerts})
record("2. A views+replies; C hard-bounces (store-level, no webhook sig)",
       "bounce accepted once; owner@C suppressed; EMAIL_SUPPRESSED alert "
       "ACTION_NOW (sole contact)",
       f"applied={applied}, suppressed={suppressed}, alerts="
       f"{[(a['kind'], a['tier'], a['detail'].get('sole_contact')) for a in bounce_alerts]}",
       "PASS" if applied and bounce_alerts
       and bounce_alerts[0]["tier"] == ns.TIER_ACTION_NOW
       and bounce_alerts[0]["detail"].get("sole_contact") is True else "BREAK",
       ev)

# ---------------------------------------------------------------------------
# Weekend — scheduler keeps running; nothing may send
# ---------------------------------------------------------------------------
before = len(provider.outbox)
weekend_results = []
for t in (FRIDAY_3PM + timedelta(hours=3), SAT, SUN):
    weekend_results.append({"now": t.isoformat(),
                            "escalations": notifications.run_escalations(t),
                            "coalesce": notifications.run_coalesced_sends(now=t),
                            "is_business_time": business_hours.is_business_time(t, PT)})
ev = harness.save_evidence("s4_step3_weekend.json",
                           {"results": weekend_results,
                            "outbox_delta": len(provider.outbox) - before})
record("3. weekend silence",
       "scheduler runs Sat+Sun: zero sends, zero new alerts",
       f"outbox delta={len(provider.outbox) - before}, results={weekend_results}",
       "PASS" if len(provider.outbox) == before else "BREAK", ev)

# ---------------------------------------------------------------------------
# Monday — the ladder
# ---------------------------------------------------------------------------
r_mon9 = notifications.run_escalations(MON_9)      # age 3bh -> nothing
before_9 = len(provider.outbox)
r_mon11 = notifications.run_escalations(MON_11)    # age 5bh -> remind B
mails_11 = provider.outbox[before_9:]
r_mon11_again = notifications.run_escalations(MON_11 + timedelta(minutes=5))
mails_11_again = provider.outbox[before_9 + len(mails_11):]
reminders = ns.list_notifications(kind=ns.KIND_RFQ_REMINDER)
ev = harness.save_evidence("s4_step4_monday_reminder.json", {
    "mon9": r_mon9, "mon11": r_mon11, "mon11_rerun": r_mon11_again,
    "mails_at_11": [{"to": m["to"], "subject": m["subject"], "body": m["body"][:400]}
                    for m in mails_11],
    "rerun_mails": len(mails_11_again),
    "reminder_rows": reminders,
    "business_time_11": business_hours.is_business_time(MON_11, PT)})
b_reminder = [m for m in mails_11 if any(B in t for t in m["to"])]
record("4. Monday reminder: ONE, consolidated, business hours, B only",
       "exactly one reminder mail to owner@B at Mon 11:00 PT; rerun adds none; "
       "A (viewed/replied) and C (bounced+suppressed) get nothing",
       f"mon9={r_mon9}, mon11={r_mon11}, mails={[m['to'] for m in mails_11]}, "
       f"subjects={[m['subject'] for m in mails_11]}, rerun added={len(mails_11_again)}",
       "PASS" if len(mails_11) == 1 and b_reminder and not mails_11_again
       and r_mon9.get("reminded") == 0 else "BREAK", ev)

# Monday 15:00 PT — escalation (age 9bh >= 8)
before_3 = len(provider.outbox)
r_mon3 = notifications.run_escalations(MON_3PM)
r_mon3_again = notifications.run_escalations(MON_3PM + timedelta(minutes=10))
r_tue = notifications.run_escalations(TUE_9)
esc_alerts = ns.list_alerts(kind=ns.ALERT_RFQ_ESCALATION, status=None)
open_queue = notifications.list_open_alerts()
ev = harness.save_evidence("s4_step5_escalation.json", {
    "mon3": r_mon3, "mon3_rerun": r_mon3_again, "tue9": r_tue,
    "escalation_alerts": esc_alerts, "open_queue_alerts": open_queue,
    "outbox_delta_after_3pm": len(provider.outbox) - before_3})
b_esc = [a for a in esc_alerts if a.get("account_id") == accounts[B]["id"]
         or a.get("supplier_domain") == B]
record("5. Monday escalation: per-account, QUEUE tier, fires once",
       "exactly one RFQ_ESCALATION alert (B), tier QUEUE; reruns + Tuesday "
       "add nothing; no supplier mail from escalation",
       f"esc alerts={[(a.get('supplier_domain') or a.get('account_id'), a['tier']) for a in esc_alerts]}, "
       f"mon3={r_mon3}, rerun={r_mon3_again}, tue={r_tue}, "
       f"outbox delta={len(provider.outbox) - before_3}",
       "PASS" if len(esc_alerts) == 1 and b_esc
       and esc_alerts[0]["tier"] == ns.TIER_QUEUE
       and len(provider.outbox) == before_3 else "BREAK", ev)

# ---------------------------------------------------------------------------
# Totals + nothing-dropped accounting
# ---------------------------------------------------------------------------
all_rfq_new = [n for n in ns.list_notifications(kind=ns.KIND_RFQ_NEW)
               if n.get("supplier_domain") in (A, B, C)]
accounting = [{"domain": n.get("supplier_domain"), "state": n.get("state"),
               "reminded_at": bool(n.get("reminded_at")),
               "escalated_at": bool(n.get("escalated_at")),
               "cancelled_at": bool(n.get("cancelled_at"))} for n in all_rfq_new]
total_alerts = ns.list_alerts(status=None)
s4_alerts = [a for a in total_alerts
             if (a.get("supplier_domain") in (A, B, C))
             or (a.get("email") or "").endswith(".example.com")
             or a.get("account_id") in {v["id"] for v in accounts.values()}]
totals = {
    "s4_supplier_emails_total": len(s4_mails()),
    "s4_supplier_email_breakdown": [{"to": m["to"], "subject": m["subject"]}
                                    for m in s4_mails()],
    "s4_concierge_alerts": [{"kind": a["kind"], "tier": a["tier"],
                             "who": a.get("email") or a.get("supplier_domain")
                             or a.get("account_id")} for a in s4_alerts],
    "rfq_new_accounting": accounting,
}
ev = harness.save_evidence("s4_step6_totals.json", totals)
dropped = [n for n in accounting
           if n["state"] not in ("SENT", "BOUNCED") and not n["cancelled_at"]
           and not n["reminded_at"] and not n["escalated_at"]
           and n["domain"] == B]
record("6. counts + nothing dropped",
       "4 supplier mails total (3 Friday RFQ_NEW + 1 Monday reminder); "
       "2 concierge alerts (C ACTION_NOW suppression, B QUEUE escalation); "
       "every notification accounted (sent/bounced/cancelled, reminded/escalated)",
       json.dumps({"emails": totals["s4_supplier_emails_total"],
                   "alerts": totals["s4_concierge_alerts"],
                   "accounting": accounting}, default=str)[:600],
       "PASS" if totals["s4_supplier_emails_total"] == 4
       and len(s4_alerts) == 2 and not dropped else "BREAK", ev)

summary = {"scenario": "S4 silence + alert discipline",
           "network": harness.network_summary(), "steps": STEPS}
harness.save_evidence("s4_steps.json", summary)
print(f"\n=== S4 done — external calls this process: "
      f"{summary['network']['external_call_count']} (expected 0) ===")
print("verdicts:", [(s["step"].split(".")[0], s["verdict"]) for s in STEPS])
