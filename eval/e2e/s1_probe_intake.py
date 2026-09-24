"""Probe: what does the intake extractor conclude for the S1 message?"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness

harness.install(live=True)
harness.isolate_stores(fresh=False)
client, api_server = harness.make_client()
provider = harness.fake_provider()

from utils import intake_channels

MSGS = [
    ("s1 clarified-full",
     "Goulds 3196 MTX pump, 1.875 inch shaft. It's a single cartridge seal — "
     "the old one on it is a Chesterton 155 cartridge seal, 1.875\". We need "
     "a like-for-like replacement mechanical seal today, line is down."),
]
out = []
for label, body in MSGS:
    ev = intake_channels.IntakeEvent(
        channel=intake_channels.IntakeChannel.EMAIL,
        tenant_key="bayfoods", sender="maintenance@bayfoods.com",
        text_body=body)
    parsed = intake_channels.parse_event_to_specs(
        ev, anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"))
    out.append({"label": label, "body": body,
                "sufficient": (parsed or {}).get("sufficient"),
                "follow_up_question": (parsed or {}).get("follow_up_question"),
                "commit_message": (parsed or {}).get("commit_message"),
                "asset_specs": (parsed or {}).get("asset_specs"),
                "confidence_summary": (parsed or {}).get("confidence_summary")})
    print(f"\n=== {label} ===")
    print(json.dumps(out[-1], indent=2, default=str)[:2500])

harness.save_evidence("s1_probe_intake.json",
                      {"probes": out, "network": harness.network_summary()})
print(f"\nexternal calls: {harness.network_summary()}")
