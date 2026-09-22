"""
Inbound REPLY ingestion self-test — reads replies from the Arkim inbox, matches each to
its sent RFQ, extracts the quote (live LLM), and QUEUES it for review; then (optionally)
CONFIRMS a queued quote into price_db.

Pairs with scripts/outreach_self_test.py: send the RFQ, reply to it in your inbox, then
run this to ingest. All against the real app DBs, so results show in the /admin inspector
(Review Queue, then Prices on confirm).

Gating: the live READ uses the same EMAIL_SEND_ENABLED gate as send (+ Gmail creds in
.env). With the gate off it reads nothing (0 replies).

Run:
  uv run python scripts/process_replies_self_test.py             # process + list queued
  uv run python scripts/process_replies_self_test.py <item_id>   # confirm one -> price_db
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

import utils.email_sender as email_sender
from utils import price_db, supplier_registry
from utils.reply_processor import confirm_quote, process_replies

# --- confirm mode: apply one queued quote to price_db ---------------------------
if len(sys.argv) > 1:
    item_id = sys.argv[1]
    ok = confirm_quote(item_id)
    print(f"confirm_quote({item_id}) -> {ok}")
    item = supplier_registry.get_review_item(item_id) or {}
    print(f"status now : {item.get('status')}")
    print(f"mfg/pn     : {item.get('manufacturer')}/{item.get('part_number')}")
    if ok:
        prices = price_db.get_cached_prices(item.get("manufacturer"), item.get("part_number"))
        print("price_db   :", json.dumps(prices, indent=2, default=str))
    sys.exit(0)

# --- process mode: read inbox, match, extract, queue ----------------------------
print(f"EMAIL_SEND_ENABLED: {email_sender.EMAIL_SEND_ENABLED}  "
      f"({'live read' if email_sender.EMAIL_SEND_ENABLED else 'STUBBED -> 0 replies'})")
summary = process_replies()  # default reader = real Gmail; default complete = live LLM
print("summary:", json.dumps(summary, indent=2))

print("\n--- open review items (pending / needs_human_review) ---")
any_open = False
for it in supplier_registry.get_review_items():
    if it.get("status") in ("pending", "needs_human_review"):
        any_open = True
        payload = it.get("payload") or {}
        print(f"id={it.get('id')} kind={it.get('kind')} status={it.get('status')} "
              f"conf={it.get('confidence')} run={it.get('run_id')} "
              f"mfg/pn={it.get('manufacturer')}/{it.get('part_number')} "
              f"price={payload.get('unit_price')} {payload.get('currency')}")
if not any_open:
    print("(none — did the reply arrive in the procurement inbox, and is the gate on?)")
else:
    print("\nTo apply one:  uv run python scripts/process_replies_self_test.py <item_id>")
