"""Boot the REAL FastAPI app on :8001 against the ISOLATED eval data, for the
Phase-3 human UI walk. Eval tooling — no app source modified.

Every outbound mail is captured by a FakeProvider that PRINTS the full body
to this console — that is how the human gets magic-link tokens and quote
links during the walk. Nothing can really send (see the Phase-0 proof).

Run:   uv run python eval/e2e/ui_walk_server.py
Stop:  Ctrl+C
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness

harness.install(live=True)          # pilot profile + network guard
harness.isolate_stores(fresh=False)  # keep the seeded scenario data
client, api_server = harness.make_client()

from utils import mail_provider


class PrintingFakeProvider(mail_provider.FakeProvider):
    def send(self, message, *, sender=None):
        result = super().send(message, sender=sender)
        m = self.outbox[-1] if result.status == "sent" else None
        print("\n" + "=" * 72)
        print(f"CAPTURED MAIL -> {getattr(message, 'to', None)}  "
              f"[{result.status}]")
        print(f"Subject: {getattr(message, 'subject', '')}")
        print("-" * 72)
        print(getattr(message, "body", ""))
        print("=" * 72 + "\n")
        return result


provider = PrintingFakeProvider()
mail_provider._OVERRIDE_PROVIDER = provider

import uvicorn

print("UI-walk backend on http://localhost:8001 (isolated data: eval/e2e/data)")
print("Mail is CAPTURED and printed here — magic links appear in this console.")
uvicorn.run(api_server.app, host="127.0.0.1", port=8001)
