"""S1 — Breakdown, like-for-like (the anchor demo). LIVE mode.

"Line 2's Goulds 3196 pump is leaking at the mechanical seal. We need a
replacement seal today."

Runs the whole path in ONE process (FakeProvider outbox is in-memory):
intake email -> identification -> Tier-1/bands -> RFQ draft/approve ->
governance release -> FakeProvider capture -> RFQ_NEW routing -> magic-link
login -> session inbox (RfqView) -> structured quote -> buyer view ->
accept/order -> escalation suppression.

Run:  uv run python eval/e2e/s1_breakdown_like_for_like.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness

harness.install(live=True)
harness.isolate_stores(fresh=False)          # keep the Phase 0 seed
client, api_server = harness.make_client()
provider = harness.fake_provider()

STEPS: list[dict] = []


def record(step: str, expected: str, observed: str, verdict: str,
           evidence: str) -> None:
    STEPS.append({"step": step, "expected": expected, "observed": observed,
                  "verdict": verdict, "evidence": evidence})
    print(f"\n[{verdict}] {step}\n  expected: {expected}\n  observed: {observed}")


def outbox_snapshot(tag: str) -> str:
    return harness.save_evidence(f"s1_outbox_{tag}.json",
                                 harness.redact_outbox(provider.outbox))


def calls() -> int:
    return len(harness.external_calls)


t0 = time.time()

# ---------------------------------------------------------------------------
# Step 1 — intake through the email channel
# ---------------------------------------------------------------------------
intake_body = ("Line 2's Goulds 3196 pump is leaking at the mechanical seal. "
               "We need a replacement seal today.")
r = client.post("/api/intake/email", json={
    "to": "intake+bayfoods@arkim.ai",
    "from": "maintenance@bayfoods.com",
    "subject": "Line 2 pump seal leak — need replacement today",
    "body": intake_body,
    "message_id": "<s1-intake-1@bayfoods.com>",
})
intake = r.json() if r.status_code < 500 else {"raw": r.text}
ev = harness.save_evidence("s1_step1_intake_response.json",
                           {"status_code": r.status_code, "body": intake,
                            "external_calls_after": calls()})
run_id = intake.get("run_id")
record("1. email intake",
       "RUN_CREATED with a run_id (or an honest clarification request)",
       f"HTTP {r.status_code}, status={intake.get('status')!r}, run_id={run_id!r}, "
       f"reason={intake.get('reason')!r}, clarify={intake.get('clarify_attrs')!r}",
       "PASS" if intake.get("status") == "RUN_CREATED" and run_id else
       ("DEGRADED" if intake.get("status") == "NEEDS_CLARIFICATION" else "BREAK"),
       ev)

# If clarification was requested, answer the agent's actual questions (shaft
# size / cartridge-vs-component / single-double / old part code) the way the
# plant manager reading the seal would. NOTE: the email channel is stateless —
# each mail is parsed alone — so the reply must be fully self-contained.
if intake.get("status") == "NEEDS_CLARIFICATION" and not run_id:
    r = client.post("/api/intake/email", json={
        "to": "intake+bayfoods@arkim.ai",
        "from": "maintenance@bayfoods.com",
        "subject": "RE: Line 2 pump seal leak",
        "body": ("Goulds 3196 MTX pump, 1.875 inch shaft. It's a single "
                 "cartridge seal — the old one on it is a Chesterton 155 "
                 "cartridge seal, 1.875\". We need a like-for-like "
                 "replacement mechanical seal today, line is down."),
        "message_id": "<s1-intake-2@bayfoods.com>",
    })
    intake = r.json()
    run_id = intake.get("run_id")
    ev = harness.save_evidence("s1_step1b_intake_retry.json",
                               {"status_code": r.status_code, "body": intake,
                                "external_calls_after": calls()})
    record("1b. intake clarification reply (self-contained)",
           "RUN_CREATED after answering the seal questions",
           f"status={intake.get('status')!r}, run_id={run_id!r}, "
           f"reason={intake.get('reason')!r}",
           "PASS" if run_id else "BREAK", ev)

# Third turn: the variant-disambig question offers "say you don't know and
# we'll source the family as-is" — take that exit, still self-contained.
if intake.get("status") == "NEEDS_CLARIFICATION" and not run_id:
    r = client.post("/api/intake/email", json={
        "to": "intake+bayfoods@arkim.ai",
        "from": "maintenance@bayfoods.com",
        "subject": "RE: RE: Line 2 pump seal leak",
        "body": ("Chesterton 155 cartridge seal for a Goulds 3196 MTX, "
                 "1.875 inch shaft, single cartridge. No other part code is "
                 "visible on it — I don't know the exact variant, please "
                 "source the family as-is. Line is down."),
        "message_id": "<s1-intake-3@bayfoods.com>",
    })
    intake = r.json()
    run_id = intake.get("run_id")
    ev = harness.save_evidence("s1_step1b2_intake_third_turn.json",
                               {"status_code": r.status_code, "body": intake,
                                "external_calls_after": calls()})
    record("1b2. intake third turn ('source family as-is')",
           "RUN_CREATED via the family-as-is exit the question offers",
           f"status={intake.get('status')!r}, run_id={run_id!r}, "
           f"reason={intake.get('reason')!r}",
           "PASS" if run_id else "BREAK", ev)

# Fallback: the in-app chat intake (multi-turn, stateful) so the rest of the
# pipeline is still evaluated even if the email channel refuses.
if not run_id:
    rc = client.post("/api/runs", json={"facility_id": "fac-stockton",
                                        "urgency_factor": 0.9})
    run_id = rc.json().get("id")
    msgs = []
    for text in (intake_body,
                 "Goulds 3196 MTX, 1.875 inch shaft, single cartridge seal — "
                 "old one is a Chesterton 155 cartridge, 1.875 inch. "
                 "Like-for-like replacement."):
        rm = client.post(f"/api/runs/{run_id}/messages", json={"content": text})
        msgs.append({"status": rm.status_code,
                     "body": rm.json() if rm.status_code < 500 else rm.text})
    ev = harness.save_evidence("s1_step1c_inapp_fallback.json",
                               {"run_id": run_id, "messages": msgs,
                                "external_calls_after": calls()})
    record("1c. in-app chat intake fallback",
           "run created; chat intake extracts specs over two turns",
           f"run_id={run_id!r}, last reply={json.dumps(msgs[-1])[:300]}",
           "PASS" if run_id else "BREAK", ev)
    rci = client.post(f"/api/runs/{run_id}/confirm-intake")
    body1 = rci.json() if rci.status_code < 500 else rci.text
    open_family_used = False
    if (rci.status_code == 422 and isinstance(body1, dict)
            and isinstance(body1.get("detail"), dict)
            and body1["detail"].get("reason") == "family_variant_unconfirmed"):
        # The UI affordance the 422 asks the frontend to show: "source the
        # family as-is" — the honest open-family commit.
        open_family_used = True
        rci = client.post(f"/api/runs/{run_id}/confirm-intake?open_family=true")
    ev = harness.save_evidence("s1_step1d_confirm_intake.json",
                               {"first_attempt": {"status": rci.status_code
                                                  if not open_family_used else 422,
                                                  "body": body1},
                                "open_family_used": open_family_used,
                                "status": rci.status_code,
                                "body": rci.json() if rci.status_code < 500 else rci.text,
                                "external_calls_after": calls()})
    record("1d. confirm-intake (fallback path)",
           "phase -> sourcing (honest open-family commit if the variant guard fires)",
           f"open_family_used={open_family_used}, HTTP {rci.status_code}: "
           f"{json.dumps(rci.json() if rci.status_code < 500 else rci.text)[:200]}",
           "PASS" if rci.status_code == 200 else "BREAK", ev)

if not run_id:
    harness.save_evidence("s1_steps.json", STEPS)
    print("S1 cannot continue without a run — stopping")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Step 2 — identification (phase should have advanced; background task ran
# inline under TestClient)
# ---------------------------------------------------------------------------
detail = None
for _ in range(3):
    r = client.get(f"/api/runs/{run_id}")
    detail = r.json()
    if detail.get("phase") in ("comparison", "error"):
        break
    time.sleep(2)
ev = harness.save_evidence("s1_step2_run_detail.json", detail)
specs = detail.get("asset_specs") or {}
record("2. identification",
       "phase=comparison; specs identify a Goulds 3196 mechanical seal, "
       "presented like-for-like (no fabricated part number)",
       f"phase={detail.get('phase')!r}, manufacturer={specs.get('manufacturer')!r}, "
       f"model={specs.get('model')!r}, part_number={specs.get('part_number')!r}, "
       f"detected_type={specs.get('detected_type')!r}, "
       f"description={str(specs.get('description'))[:120]!r}, "
       f"no_exact_match={detail.get('no_exact_match')!r}",
       "PASS" if detail.get("phase") == "comparison" else "BREAK",
       ev)

# ---------------------------------------------------------------------------
# Step 3 — Tier-1 matching + evidence bands
# ---------------------------------------------------------------------------
sr = detail.get("sourcing_results") or {}
findings = detail.get("findings") or []
outreach = detail.get("outreachTargets") or detail.get("outreach_targets") or {}
tier1 = sr.get("tier1") or []
dxp_candidates = [c for c in (tier1 + findings)
                  if "dxp" in (c.get("vendorName", "").lower())]
band_summary = [{"vendor": c.get("vendorName"), "band": c.get("band"),
                 "tier": c.get("tier"), "evidenceState": c.get("evidenceState"),
                 "evidenceQuality": c.get("evidenceQuality"),
                 "registryBacked": c.get("registryBacked"),
                 "explanation": c.get("tier1MatchExplanation"),
                 "price": c.get("price"), "url": c.get("url"),
                 "foundPartNumber": c.get("foundPartNumber"),
                 "isMock": c.get("isMock")}
                for c in (findings or tier1)]
ev = harness.save_evidence("s1_step3_bands.json",
                           {"tier1": tier1, "findings": findings,
                            "outreachTargets": outreach,
                            "band_summary": band_summary})
record("3. Tier-1 + banding",
       "DXP surfaces as a Tier-1/registry-backed candidate with a band and "
       "provenance; bands carried on findings",
       f"tier1 n={len(tier1)}, findings n={len(findings)}, "
       f"DXP present={bool(dxp_candidates)}, summary={json.dumps(band_summary)[:400]}",
       "PASS" if dxp_candidates else "BREAK",
       ev)

# ---------------------------------------------------------------------------
# Step 4 — RFQ draft -> approve -> governance -> FakeProvider capture
# ---------------------------------------------------------------------------
target = (dxp_candidates or tier1 or findings)[0] if (dxp_candidates or tier1 or findings) else None
draft_id = None
if target:
    r = client.post(f"/api/runs/{run_id}/rfq-draft",
                    json={"candidate_id": target["id"], "tier": target.get("tier", 1)})
    draft = r.json() if r.status_code < 500 else {"raw": r.text}
    draft_id = draft.get("draft_id")
    ra = client.post(f"/api/rfq-drafts/{draft_id}/approve",
                     json={"approved_by": "tom@arkim.ai"}) if draft_id else None
    # direct send must be refused under governance (409) …
    rs = client.post(f"/api/rfq-drafts/{draft_id}/send") if draft_id else None
    # … and released through the admin release queue
    before = len(provider.outbox)
    rq = client.get("/api/admin/send-governance/release-queue",
                    headers=harness.admin_headers())
    rr = client.post("/api/admin/send-governance/release-queue/release",
                     headers=harness.admin_headers(),
                     json={"draft_ids": [draft_id], "released_by": "tom@arkim.ai"})
    release = rr.json() if rr.status_code < 500 else {"raw": rr.text}
    rfq_mails = [m for m in provider.outbox[before:]
                 if "dxpe.com" in json.dumps(m.get("to", []))]
    ev = harness.save_evidence("s1_step4_rfq.json", {
        "draft_response": draft, "approve": ra.json() if ra else None,
        "direct_send_status_code": rs.status_code if rs else None,
        "direct_send_body": rs.json() if rs else None,
        "release_queue": rq.json(), "release": release,
        "captured_rfq_mails": rfq_mails})
    outbox_snapshot("after_step4")
    record("4. RFQ via governance, captured not sent",
           "draft created+approved; direct /send 409s; release queue delivers; "
           "RFQ captured in FakeProvider to DXP's contact",
           f"draft_id={draft_id!r}, direct_send={rs.status_code if rs else None}, "
           f"release={json.dumps(release)[:200]}, rfq_mails_to_dxp={len(rfq_mails)}, "
           f"to={[m['to'] for m in rfq_mails]}",
           "PASS" if (draft_id and rs is not None and rs.status_code == 409
                      and len(rfq_mails) >= 1) else "BREAK",
           ev)
else:
    record("4. RFQ via governance", "a candidate to RFQ", "no candidate available",
           "BREAK", "s1_step3_bands.json")

# ---------------------------------------------------------------------------
# Step 5 — RFQ_NEW routing: ONLY OWNER + ADMIN
# ---------------------------------------------------------------------------
from utils import notifications_store as ns
rfq_new = [n for n in ns.list_notifications(kind=ns.KIND_RFQ_NEW)
           if n.get("supplier_domain") == "dxpe.com"]
recipients = sorted({n.get("recipient") for n in rfq_new})
notif_mails = [m for m in provider.outbox
               if m.get("metadata", {}).get("notification_kind") == "RFQ_NEW"]
ev = harness.save_evidence("s1_step5_rfq_new_routing.json",
                           {"notifications": rfq_new,
                            "delivered_to": [m["to"] for m in notif_mails]})
expected_set = {"owner@dxpe.com", "admin@dxpe.com"}
record("5. RFQ_NEW routing (S1: OWNER+ADMIN only)",
       f"RFQ_NEW rows + delivered mail to exactly {sorted(expected_set)}; "
       "MEMBERs excluded",
       f"store recipients={recipients}, delivered={[m['to'] for m in notif_mails]}",
       "PASS" if set(recipients) == expected_set and
       all(set(m["to"]) <= expected_set for m in notif_mails) and notif_mails
       else "BREAK",
       ev)

# ---------------------------------------------------------------------------
# Step 5b — POST-HARDENING (arc 5 T8, F-09/F-10): supplier-facing mail says
# what the RFQ is, and leaks no run UUID / internal vocabulary.
# ---------------------------------------------------------------------------
import re as _re
_UUID_RE = _re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", _re.I)
_DENY = ("class-matched", "no brand row", "noun_class", "tier1_lifecycle",
         "class_gate")
supplier_mails = [m for m in provider.outbox
                  if any("dxpe.com" in t for t in m.get("to", []))]
rfq_new_mails = [m for m in supplier_mails
                 if m.get("metadata", {}).get("notification_kind") == "RFQ_NEW"]
mfg = (specs.get("manufacturer") or "")
identity_tokens = [t for t in (mfg, specs.get("model"),
                               specs.get("part_number")) if t]
named = [m for m in rfq_new_mails
         if any(str(t) in (m.get("subject", "") + m.get("body", ""))
                for t in identity_tokens)]
leaks = [{"to": m["to"], "subject": m["subject"],
          "uuid": bool(_UUID_RE.search(m.get("subject", "") + m.get("body", ""))),
          "internal_vocab": [d for d in _DENY
                             if d in (m.get("subject", "") + m.get("body", ""))]}
         for m in supplier_mails
         if _UUID_RE.search(m.get("subject", "") + m.get("body", ""))
         or any(d in (m.get("subject", "") + m.get("body", "")) for d in _DENY)]
ev = harness.save_evidence("s1_step5b_mail_content.json", {
    "supplier_mails": [{"to": m["to"], "subject": m["subject"],
                        "kind": m.get("metadata", {}).get("notification_kind"),
                        "body": m.get("body", "")[:600]} for m in supplier_mails],
    "identity_tokens": identity_tokens, "rfq_new_named_part": len(named),
    "leaks": leaks})
record("5b. supplier mail content (arc5 T8)",
       "RFQ_NEW mail names the part identity; NO supplier-facing mail carries "
       "a run UUID or internal vocabulary (class-matched / noun_class / ...)",
       f"rfq_new mails={len(rfq_new_mails)}, naming the part={len(named)}, "
       f"leaking mails={json.dumps(leaks)[:300]}",
       "PASS" if rfq_new_mails and len(named) == len(rfq_new_mails)
       and not leaks else "BREAK", ev)

# ---------------------------------------------------------------------------
# Step 6 — supplier signs in via magic link taken from captured mail
# ---------------------------------------------------------------------------
before = len(provider.outbox)
r = client.post("/api/supplier/auth/request-link",
                json={"email": "owner@dxpe.com"})
link_mails = provider.outbox[before:]
token = None
for m in link_mails:
    if "/supplier/verify?token=" in m.get("body", ""):
        token = m["body"].split("/supplier/verify?token=", 1)[1].split()[0].strip()
        break
session_token = None
if token:
    rv = client.post("/api/supplier/auth/verify", json={"token": token},
                     headers={"Origin": "http://localhost:3000"})
    if rv.status_code == 200:
        session_token = rv.json().get("token")
ev = harness.save_evidence("s1_step6_login.json", {
    "request_link_status": r.status_code, "auth_mails": len(link_mails),
    "auth_mail_to": [m["to"] for m in link_mails],
    "auth_config_set": [m.get("configuration_set") for m in link_mails],
    "verify_status": rv.status_code if token else None,
    "cookie_set": bool(rv.cookies.get("gofer_supplier_session")) if token else None,
    "token_found": bool(token), "session_established": bool(session_token)})
record("6. magic-link login",
       "auth mail captured (tracking-OFF config set); verify returns 200, "
       "session cookie + bearer issued",
       f"auth mails={len(link_mails)} to={[m['to'] for m in link_mails]}, "
       f"config_set={[m.get('configuration_set') for m in link_mails]}, "
       f"session={'yes' if session_token else 'NO'}",
       "PASS" if session_token else "BREAK", ev)

auth_headers = {"Authorization": f"Bearer {session_token}"} if session_token else {}

# ---------------------------------------------------------------------------
# Step 7 — session inbox shows the RFQ; RfqView is written
# ---------------------------------------------------------------------------
r = client.get("/api/supplier/requests", headers=auth_headers)
reqs = r.json().get("requests", []) if r.status_code == 200 else []
ours = [q for q in reqs if q.get("run_id") == run_id]
viewed = ns.rfq_viewed(run_id, supplier_domain="dxpe.com")
ev = harness.save_evidence("s1_step7_inbox.json",
                           {"status": r.status_code, "requests": reqs,
                            "rfq_viewed": bool(viewed)})
record("7. session inbox + RfqView",
       "the RFQ for this run appears in /api/supplier/requests; an RfqView "
       "row is recorded for dxpe.com",
       f"inbox n={len(reqs)}, this run present={bool(ours)}, "
       f"entry={json.dumps(ours[0]) if ours else None}, rfq_viewed={bool(viewed)}",
       "PASS" if ours and viewed else "BREAK", ev)

# ---------------------------------------------------------------------------
# Step 8 — supplier submits a structured quote through the session
# ---------------------------------------------------------------------------
quote_payload = {"run_id": run_id, "quote_number": "DXP-EVAL-0091",
                 "unit_price": 189.0, "quantity": 1, "lead_time": "2 days",
                 "part_number": None, "freight": None, "valid_until": None,
                 "notes": "In stock at Sacramento branch"}
r = client.post("/api/supplier/quotes", headers=auth_headers, json=quote_payload)
quote_resp = r.json() if r.status_code < 500 else {"raw": r.text}
ev = harness.save_evidence("s1_step8_quote_submit.json",
                           {"status": r.status_code, "payload": quote_payload,
                            "response": quote_resp})
record("8. structured quote via session",
       "quote accepted (ok, status active) and attributed to the member/domain",
       f"HTTP {r.status_code}, response={json.dumps(quote_resp)[:250]}",
       "PASS" if r.status_code == 200 and quote_resp.get("ok") else "BREAK", ev)

# ---------------------------------------------------------------------------
# Step 9 — the quote reaches the buyer's view, correctly attributed
# ---------------------------------------------------------------------------
r = client.get(f"/api/runs/{run_id}")
detail2 = r.json()
all_cands = ((detail2.get("sourcing_results") or {}).get("tier1", []) +
             (detail2.get("sourcing_results") or {}).get("tier2", []) +
             (detail2.get("sourcing_results") or {}).get("tier3", []) +
             (detail2.get("findings") or []))
quoted = [c for c in all_cands if c.get("evidenceState") == "quoted"]
dxp_quoted = [c for c in quoted if "dxp" in c.get("vendorName", "").lower()]
ev = harness.save_evidence("s1_step9_buyer_view.json",
                           {"detail": detail2, "quoted_candidates": quoted})
record("9. buyer sees the quote",
       "a DXP candidate shows evidenceState='quoted' with price 189.0 and "
       "quote provenance (quoteId), not a fabricated price",
       f"quoted candidates={[(c.get('vendorName'), c.get('price'), c.get('quoteId'), c.get('leadTime')) for c in quoted]}",
       "PASS" if dxp_quoted and any(abs((c.get("price") or 0) - 189.0) < 0.01
                                    for c in dxp_quoted) else "BREAK", ev)

# ---------------------------------------------------------------------------
# Step 10 — buyer accepts; order state advances
# ---------------------------------------------------------------------------
accept_target = (dxp_quoted or quoted or dxp_candidates or all_cands)[0] if all_cands else None
step10_ev = {}
if accept_target:
    rsel = client.post(f"/api/runs/{run_id}/select-candidate",
                       json={"candidate_id": accept_target["id"],
                             "tier": accept_target.get("tier", 1)})
    step10_ev["select"] = {"status": rsel.status_code, "body": rsel.json()}
    rap = client.post(f"/api/runs/{run_id}/approve",
                      json={"approver_name": "Dana Plant-Manager",
                            "approver_role": "maintenance_manager",
                            "notes": "line down, approved"})
    step10_ev["approve"] = {"status": rap.status_code, "body": rap.json()}
    # second approval if required
    rd = client.get(f"/api/runs/{run_id}").json()
    if rd.get("phase") == "pending_second_approval":
        rap2 = client.post(f"/api/runs/{run_id}/approve",
                           json={"approver_name": "Sam Controller",
                                 "approver_role": "finance", "notes": "ok"})
        step10_ev["approve2"] = {"status": rap2.status_code, "body": rap2.json()}
        rd = client.get(f"/api/runs/{run_id}").json()
    rex = client.post(f"/api/runs/{run_id}/execute")
    step10_ev["execute"] = {"status": rex.status_code,
                            "body": rex.json() if rex.status_code < 500 else rex.text}
    rorders = client.get(f"/api/runs/{run_id}/orders")
    orders_resp = rorders.json() if rorders.status_code == 200 else {}
    # the endpoint returns an envelope {run_id, count, orders: [...]}
    orders = orders_resp.get("orders", orders_resp) \
        if isinstance(orders_resp, dict) else orders_resp
    step10_ev["orders"] = orders_resp
    rd2 = client.get(f"/api/runs/{run_id}").json()
    step10_ev["final_phase"] = rd2.get("phase")
    order_prices = [(o.get("status"), o.get("unit_price") or o.get("price"),
                     o.get("vendor_name") or o.get("vendorName"),
                     o.get("quote_id") or o.get("quoteId")) for o in orders] \
        if isinstance(orders, list) else orders
    ev = harness.save_evidence("s1_step10_accept_order.json", step10_ev)
    # POST-HARDENING (arc 5 T1, F-07): the order must carry the accepted
    # quote's price AND record the quote id it came from.
    priced = [o for o in orders if isinstance(o, dict)
              and abs(((o.get("unit_price") or o.get("price") or 0)) - 189.0) < 0.01]
    with_quote_id = [o for o in priced if o.get("quote_id") or o.get("quoteId")]
    record("10. accept -> order (arc5 T1: quote-priced)",
           "approval path completes; an order exists AT THE QUOTED PRICE "
           "(189.0) and records the quote id it came from",
           f"select={rsel.status_code}, approve={rap.status_code}, "
           f"execute={rex.status_code}, final_phase={rd2.get('phase')!r}, "
           f"orders(status,price,vendor,quote_id)={json.dumps(order_prices)[:300]}, "
           f"quote_id_recorded={bool(with_quote_id)}",
           "PASS" if priced and with_quote_id else
           ("DEGRADED" if priced else "BREAK"),
           ev)
else:
    record("10. accept -> order", "an acceptable candidate", "none", "BREAK", "-")

# ---------------------------------------------------------------------------
# Step 11 — no escalation fires for DXP (RFQ was viewed)
# ---------------------------------------------------------------------------
from utils import business_hours, notifications
late = business_hours.business_hours_after(datetime.now(timezone.utc), 10.0)
before = len(provider.outbox)
esc = notifications.run_escalations(late)
alerts = ns.list_alerts(kind=ns.ALERT_RFQ_ESCALATION, status=None)
dxp_alerts = [a for a in alerts if a.get("supplier_domain") == "dxpe.com"
              or a.get("account_id")]
reminders = [n for n in ns.list_notifications(kind=ns.KIND_RFQ_REMINDER)
             if n.get("supplier_domain") == "dxpe.com"]
ev = harness.save_evidence("s1_step11_escalation.json",
                           {"scheduler_now": late.isoformat(), "result": esc,
                            "rfq_escalation_alerts": alerts,
                            "dxp_reminders": reminders,
                            "outbox_grew": len(provider.outbox) - before})
record("11. no escalation for viewed RFQ",
       "run_escalations at +10 business hours: no reminder, no escalation "
       "alert for dxpe.com (RfqView suppresses)",
       f"result={esc}, dxp reminders={len(reminders)}, "
       f"escalation alerts={len(alerts)}, outbox grew={len(provider.outbox)-before}",
       "PASS" if not reminders and not alerts and
       len(provider.outbox) == before else "BREAK", ev)

# ---------------------------------------------------------------------------
# Wrap up
# ---------------------------------------------------------------------------
summary = {
    "scenario": "S1 breakdown like-for-like",
    "run_id": run_id,
    "duration_s": round(time.time() - t0, 1),
    "network": harness.network_summary(),
    "steps": STEPS,
}
harness.save_evidence("s1_steps.json", summary)
outbox_snapshot("final")
print(f"\n=== S1 done in {summary['duration_s']}s — external calls: "
      f"{summary['network']['external_call_count']}/{harness.MAX_EXTERNAL_CALLS} "
      f"(by host: {summary['network']['by_host']}) ===")
print(f"verdicts: {[ (s['step'].split('.')[0], s['verdict']) for s in STEPS ]}")
