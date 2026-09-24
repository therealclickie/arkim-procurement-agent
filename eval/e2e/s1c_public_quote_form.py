"""S1c — exercise the previously-unreached public quote-form seam (GET side).

The S1 RFQ mail to sales@dxpe.com carries a /quote/{token} link (QUOTE_SUBMIT_V1).
The previous eval never opened it. This probe resolves the token read-only:
GET the form payload and check it describes the S1 RFQ honestly. The POST side
is deliberately NOT exercised, to leave the S1 walk data (the $189 accepted
quote and its placed order) untouched — recorded as partial seam coverage.

Run:  uv run python eval/e2e/s1c_public_quote_form.py    (0 external calls)
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness

harness.install(live=True)
harness.isolate_stores(fresh=False)
client, api_server = harness.make_client()

outbox = json.load(open(os.path.join(harness.EVIDENCE, "s1_outbox_final.json"),
                        encoding="utf-8"))
token = None
for m in outbox:
    mm = re.search(r"/quote/([A-Za-z0-9_\-\.]+)", m.get("body", ""))
    if mm and any("sales@dxpe.com" in t for t in m.get("to", [])):
        token = mm.group(1)
        break

result = {"token_found": bool(token)}
if token:
    r = client.get(f"/api/quote/{token}")
    body = r.json() if r.status_code < 500 else r.text
    result.update({"status": r.status_code, "body": body})
    rbad = client.get("/api/quote/not-a-real-token")
    result["invalid_token_status"] = rbad.status_code
harness.save_evidence("s1c_public_quote_form.json", result)
print(json.dumps(result, indent=2, default=str)[:1500])
print("external calls:", len(harness.external_calls))
