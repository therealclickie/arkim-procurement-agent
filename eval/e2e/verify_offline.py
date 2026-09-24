"""Verify pass — zero-external-call checks (F-07, F-11 mechanism, F-12, F-15,
F-02, F-03, F-04). Reads committed eval data READ-ONLY; writes only
evidence/verify/verify_offline_checks.json.

Run:  uv run python eval/e2e/verify_offline.py
"""
from __future__ import annotations

import glob
import json
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["TAVILY_API_KEY"] = ""


def ro(db: str) -> sqlite3.Connection:
    path = os.path.join(HERE, "data", db).replace("\\", "/")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


out: dict = {}

# F-07 — order row vs active quote for the same run/domain
run_id = "e0489093-4d8c-4563-ab8f-cb6c789ed6db"
out["F-07"] = {
    "orders": [dict(zip(("id", "vendor", "domain", "unit_price", "source", "status"), r))
               for r in ro("orders.sqlite").execute(
                   "select id,vendor_name,supplier_domain,unit_price,source,status "
                   "from orders where run_id=?", (run_id,))],
    "quotes": [dict(zip(("id", "domain", "unit_price", "lead_time", "status"), r))
               for r in ro("quotes.sqlite").execute(
                   "select id,supplier_domain,unit_price,lead_time,status "
                   "from quotes where run_id=?", (run_id,))],
    "order_path_references_quote_store": any(
        "quote_store" in open(p, encoding="utf-8").read()
        for p in (os.path.join(ROOT, "utils", "orders.py"),
                  os.path.join(ROOT, "utils", "procurement_agent", "agents",
                               "procurement_agent.py"))),
}

# F-11 mechanism / F-12 — deterministic PN classifier vs the LLM badge
from utils.sourcing_archieved.scoring import _classify_pn_match  # noqa: E402
out["F-11_F-12_classifier"] = {
    f: _classify_pn_match("6205-2RS C3", f, "", "SKF")
    for f in ("6205-2RS", "6205-2RS C3", "6205-2RS-C3", "6205-2RSH/C3")}

# F-15 — the only sufficiency guard returns None for the S3 spec-less gauge
from utils.procurement_agent.agents.intake_agent import family_disambig_block  # noqa: E402
s3 = json.load(open(os.path.join(HERE, "evidence", "s3_step2_confirm_attempt.json"),
                    encoding="utf-8"))
out["F-15"] = {"confirm_status": s3["status"],
               "family_disambig_block_on_s3_specs": family_disambig_block(
                   s3["sourced"]["specs_at_sourcing"])}

# F-02 — tracked next.config default
cfg = open(os.path.join(ROOT, "frontend", "next.config.ts"), encoding="utf-8").read()
out["F-02"] = {"default": re.search(r'NEXT_PUBLIC_API_URL \?\? "([^"]+)"', cfg).group(1)}

# F-03 — auth mail refused when the auth configuration set is unset
from utils import mail_provider  # noqa: E402
os.environ.pop(mail_provider.ENV_CONFIG_SET_AUTH, None)


class _Msg:
    metadata = {"auth_mail": True}


out["F-03"] = {"message_configuration_set": list(
    mail_provider.message_configuration_set(_Msg()))}

# F-04 — hard-coded _DATA_DIR in store modules, and no env override
hits, env_override = [], False
for p in glob.glob(os.path.join(ROOT, "utils", "**", "*.py"), recursive=True):
    if os.sep + "tests" + os.sep in p:
        continue
    src = open(p, encoding="utf-8").read()
    if re.search(r'^_DATA_DIR\s*=.*"data"', src, re.M):
        hits.append(os.path.relpath(p, ROOT))
    if re.search(r'_DATA_DIR\s*=.*(environ|getenv)', src):
        env_override = True
out["F-04"] = {"modules_with_hardcoded_data_dir": sorted(hits),
               "any_env_override": env_override}

os.makedirs(os.path.join(HERE, "evidence", "verify"), exist_ok=True)
with open(os.path.join(HERE, "evidence", "verify", "verify_offline_checks.json"),
          "w", encoding="utf-8") as f:
    json.dump(out, f, indent=2, default=str)
print(json.dumps(out, indent=2, default=str))
