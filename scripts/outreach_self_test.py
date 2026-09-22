"""
Gated OUTREACH self-test — drives the REAL Tier 3 outreach send flow
(utils.rfq_send.send_rfq) to a mailbox YOU CONTROL, so you can exercise
outreach + reply ingestion end-to-end without contacting a real supplier.

What it does (all against the real app DBs, so it shows in the /admin inspector):
  1. Records a sourcing run in the audit log with part specs (so a confirmed quote
     can later be keyed into price_db).
  2. Seeds a resolved PRIMARY contact in supplier_registry for the recipient's domain,
     so recipient_set() resolves the send to your address.
  3. Sends ONE approved RFQ through send_rfq(), which:
       - respects EMAIL_SEND_ENABLED (now env-driven, default OFF). OFF -> records a
         'stubbed' sent_messages row and sends NOTHING; ON (.env EMAIL_SEND_ENABLED=True)
         -> a REAL send via GmailSender.
       - records the sent_messages row (message_id / thread_id) that the inbound reply
         matcher joins on.

SAFETY: recipient defaults to a controlled test inbox; override with OUTREACH_TEST_TO.
NEVER point this at a real supplier — it sends to whatever OUTREACH_TEST_TO is when the
gate is on.

Run:
  uv run python scripts/outreach_self_test.py
Then reply to the email in that inbox, and run:
  uv run python scripts/process_replies_self_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

import utils.email_sender as email_sender
from utils import supplier_registry
from utils.audit_log import write_audit_log
from utils.rfq_send import Approval, send_rfq

TO = os.environ.get("OUTREACH_TEST_TO", "tom.dickie89@gmail.com")
DOMAIN = TO.split("@")[-1]
RUN_ID = os.environ.get("OUTREACH_TEST_RUN_ID", "rfq-ingest-test-001")
SPECS = {"manufacturer": "SKF", "part_number": "6205-2RS1"}

print("=== Outreach self-test (real send_rfq flow, gated) ===")
print(f"EMAIL_SEND_ENABLED : {email_sender.EMAIL_SEND_ENABLED}  "
      f"({'WILL SEND' if email_sender.EMAIL_SEND_ENABLED else 'stub only — nothing will send'})")
print(f"To (controlled)    : {TO}")
print(f"Domain             : {DOMAIN}")
print(f"Run                : {RUN_ID}")

# 1) Run with specs so a confirmed quote can key price_db on confirm.
write_audit_log({
    "sourcing_run_id": RUN_ID, "asset_specs_json": SPECS,
    "input_summary": "SKF 6205-2RS1 — outreach self-test",
    "workflow_mode": "tier3_rfq_sent", "agent_version": "outreach-test",
})

# 2) Seed a resolved PRIMARY contact for the domain so recipient_set() -> To: [TO].
supplier_registry.upsert_primary_contact(DOMAIN, {
    "primary_contact_email": TO,
    "primary_contact_name": "Tom Dickie (test)",
    "primary_contact_status": "resolved",
    "primary_contact_source": "manual_test",
})

# 3) Send one approved RFQ through the real outreach flow.
candidate = {"vendor_name": "Test Supplier (Dickie)", "source_url": f"https://{DOMAIN}"}
draft = (
    "Subject: Arkim RFQ — SKF 6205-2RS1 bearing\n\n"
    "Hello,\n\nArkim procurement is sourcing the part below and would appreciate your "
    "best unit price, lead time, and availability:\n\n"
    "  - Part: SKF 6205-2RS1 deep-groove ball bearing\n"
    "  - Quantity: 24\n"
    "  - Required: within 2 weeks\n\n"
    "Please reply to this email with your quotation.\n\n"
    "Thanks,\nArkim Procurement"
)
approval = Approval(approved_by="tom@arkim.ai")

result = send_rfq(candidate, draft, approval, run_id=RUN_ID)

print("\n--- send_rfq result ---")
for k in ("status", "sent", "vendor_name", "domain", "recipients", "sent_message_id"):
    print(f"  {k}: {result.get(k)}")
sr = result.get("send_result")
if sr:
    print(f"  send_result: status={sr.status} message_id={sr.message_id} "
          f"thread_id={sr.thread_id} error={sr.error}")

status = result.get("status")
if status == "sent":
    print(f"\n=> SENT to {TO}. Reply to it, then run:")
    print("   uv run python scripts/process_replies_self_test.py")
elif status == "stubbed":
    print("\n=> STUBBED (gate off). Set EMAIL_SEND_ENABLED=True in .env (and restart) to "
          "actually send.")
elif status == "no_recipients":
    print("\n=> NO RECIPIENTS — the primary contact seed didn't resolve. Check the domain.")
else:
    print(f"\n=> {status} — see result above.")
