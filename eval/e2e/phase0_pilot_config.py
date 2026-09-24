"""Phase 0 — pilot configuration: boot check, MAIL SAFETY PROOF, seed.

Run:  uv run python eval/e2e/phase0_pilot_config.py
Writes evidence to eval/e2e/evidence/phase0_*.json and seeds eval/e2e/data/.
Exit code non-zero if the mail-safety proof fails (brief rule 3: STOP).
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness

harness.install(live=True)
harness.isolate_stores(fresh=True)

results: dict = {"phase": "0", "checks": []}
FAILED = False


def check(name: str, ok: bool, detail: str) -> None:
    global FAILED
    results["checks"].append({"check": name, "ok": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    if not ok:
        FAILED = True


# ---------------------------------------------------------------------------
# 0.2 Boot check — app imports under the pilot profile, health responds
# ---------------------------------------------------------------------------
client, api_server = harness.make_client()
r = client.get("/api/health")
check("boot.health", r.status_code == 200, f"GET /api/health -> {r.status_code} {r.text[:200]}")

# Import-bound flags actually bound ON under the profile
from utils import supplier_registry, claim_tokens
from utils.sourcing_archieved import scoring as _scoring
check("flags.TIER1_V2_bound", supplier_registry.TIER1_V2 is True,
      f"supplier_registry.TIER1_V2={supplier_registry.TIER1_V2}")
check("flags.SCORING_V2_bound", _scoring.SCORING_V2 is True,
      f"scoring.SCORING_V2={_scoring.SCORING_V2}")
check("flags.CLAIM_TOKENS_bound", claim_tokens.CLAIM_TOKENS_ENABLED is True,
      f"claim_tokens.CLAIM_TOKENS_ENABLED={claim_tokens.CLAIM_TOKENS_ENABLED}")
check("flags.SUPPLIER_PORTAL_bound", api_server.SUPPLIER_PORTAL_V1 is True,
      f"api_server.SUPPLIER_PORTAL_V1={api_server.SUPPLIER_PORTAL_V1}")
check("flags.DEMO_MODE_off", api_server.DEMO_MODE is False,
      f"api_server.DEMO_MODE={api_server.DEMO_MODE}")

# Isolation sanity: every store path is under eval/e2e/data
import importlib
bad_paths = []
for mod_name, fname in harness._STORE_MODULES:
    mod = importlib.import_module(mod_name)
    if not str(getattr(mod, "_DB_PATH", "")).startswith(harness.DATA):
        bad_paths.append(f"{mod_name}={getattr(mod, '_DB_PATH', None)}")
from utils import known_parts as _kp, price_db as _pdb
from utils.procurement_agent.state import persistence as _pers
for label, path in (("known_parts", _kp._DB_PATH), ("price_db", _pdb._DB_PATH),
                    ("persistence", _pers._DB_PATH)):
    if not str(path).startswith(harness.DATA):
        bad_paths.append(f"{label}={path}")
check("isolation.store_paths", not bad_paths,
      "all store paths under eval/e2e/data" if not bad_paths else f"OUTSIDE: {bad_paths}")

# POST-HARDENING (arc 5 T10, F-04): GOFER_DATA_DIR must have isolated every
# store NATIVELY — before the harness monkeypatch touched anything.
t10_misses = {k: v for k, v in harness.t10_native_isolation.items()
              if not v["under_eval_data"]}
results["t10_native_isolation"] = harness.t10_native_isolation
check("hardening.F04_gofer_data_dir_native", not t10_misses,
      f"GOFER_DATA_DIR natively isolated all {len(harness.t10_native_isolation)} "
      "store modules (monkeypatch was a no-op)" if not t10_misses
      else f"modules NOT natively isolated: {t10_misses}")

# api_server's handoffs seed path must also follow GOFER_DATA_DIR (gate J9).
check("hardening.F04_handoffs_path",
      str(api_server._HANDOFFS_PATH).startswith(harness.DATA),
      f"api_server._HANDOFFS_PATH={api_server._HANDOFFS_PATH}")

# POST-HARDENING (arc 5 T9, F-02): the frontend default API URL must be the
# port the backend serves on (:8001), with no .env.local needed.
_ncfg = open(os.path.join(harness.ROOT, "frontend", "next.config.ts"),
             encoding="utf-8").read()
check("hardening.F02_frontend_default_8001",
      "http://localhost:8001" in _ncfg and "http://localhost:8000" not in _ncfg,
      "next.config.ts defaults NEXT_PUBLIC_API_URL to :8001 (no stale :8000)")

# POST-HARDENING (arc 5 T7, F-03): boot-refusal guard. With SUPPLIER_ACCOUNTS_V1
# on under MAIL_PROVIDER=ses and SES_CONFIGURATION_SET_AUTH unset, api_server
# must refuse to import, naming the variable. Under fake, or with the var set,
# it must boot. Probed in SUBPROCESSES (import-time guard) against a scratch
# GOFER_DATA_DIR so nothing touches dev or eval data; all keys blanked.
import subprocess, tempfile
_boot_code = "import api_server; print('BOOTED')"
def _boot_probe(extra_env: dict) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as scratch:
        env = dict(os.environ)
        for k in ("ANTHROPIC_API_KEY", "TAVILY_API_KEY", "APOLLO_API_KEY",
                  "PARALLEL_API_KEY", "EMAIL_SEND_ENABLED"):
            env[k] = ""
        env["GOFER_DATA_DIR"] = scratch
        env.pop("SES_CONFIGURATION_SET_AUTH", None)
        env.update(extra_env)
        p = subprocess.run([sys.executable, "-c", _boot_code],
                           cwd=harness.ROOT, env=env,
                           capture_output=True, text=True, timeout=120)
        return p.returncode, (p.stdout + p.stderr)[-800:]

rc1, out1 = _boot_probe({"SUPPLIER_ACCOUNTS_V1": "1", "MAIL_PROVIDER": "ses"})
check("hardening.F03_ses_unset_refuses_boot",
      rc1 != 0 and "SES_CONFIGURATION_SET_AUTH" in out1
      and "Refusing to start" in out1,
      f"ses+accounts+var-unset -> exit {rc1}, names the variable: "
      f"{'SES_CONFIGURATION_SET_AUTH' in out1}")
rc2, out2 = _boot_probe({"SUPPLIER_ACCOUNTS_V1": "1", "MAIL_PROVIDER": "ses",
                         "SES_CONFIGURATION_SET_AUTH": "probe-auth-set"})
check("hardening.F03_ses_set_boots", rc2 == 0 and "BOOTED" in out2,
      f"ses+accounts+var-set -> exit {rc2}")
rc3, out3 = _boot_probe({"SUPPLIER_ACCOUNTS_V1": "1", "MAIL_PROVIDER": "fake"})
check("hardening.F03_fake_provider_exempt_at_boot", rc3 == 0 and "BOOTED" in out3,
      f"fake+accounts+var-unset -> exit {rc3} (FakeProvider needs no auth set)")
results["boot_guard_probes"] = {
    "ses_unset": {"exit": rc1, "tail": out1},
    "ses_set": {"exit": rc2, "tail": out2},
    "fake_unset": {"exit": rc3, "tail": out3}}

# ---------------------------------------------------------------------------
# 0.4 MAIL SAFETY PROOF — four independent layers, each demonstrated
# ---------------------------------------------------------------------------
from utils import email_sender, mail_provider, send_governance
from utils.email_sender import EmailMessage

provider = harness.fake_provider()

# Layer 1: under the pilot profile the selected transport is FakeProvider
# (both via the explicit override and via config selection).
saved_override = mail_provider._OVERRIDE_PROVIDER
mail_provider._OVERRIDE_PROVIDER = None
selected = mail_provider.active_provider()
check("mail.layer1_fake_transport", isinstance(selected, mail_provider.FakeProvider),
      f"active_provider() under profile -> {type(selected).__name__} (MAIL_PROVIDER=fake)")
mail_provider._OVERRIDE_PROVIDER = saved_override

# Layer 2: even if MAIL_PROVIDER were misconfigured to 'ses', SES cannot
# construct (AWS_REGION blank) — fail-soft error, zero network.
os.environ["MAIL_PROVIDER"] = "ses"
mail_provider._OVERRIDE_PROVIDER = None
ses = mail_provider.active_provider()
ses_result = ses.send(EmailMessage(to=["proof@supplier-a.example.com"],
                                   subject="layer2 probe", body="x"))
check("mail.layer2_ses_cannot_construct",
      isinstance(ses, mail_provider.SesProvider) and ses_result.status == "error",
      f"SesProvider.send -> status={ses_result.status!r} error={ses_result.error!r}")
os.environ["MAIL_PROVIDER"] = "fake"
mail_provider._OVERRIDE_PROVIDER = saved_override

# Layer 3: even if NOTIFICATIONS_V1 were off (Gmail path), Gmail credentials
# are blanked — no service can be built, fail-soft error, zero network.
os.environ["NOTIFICATIONS_V1"] = ""
mail_provider._OVERRIDE_PROVIDER = None
send_governance.allowlist_add("supplier-a.example.com", added_by="eval-phase0",
                              note="mail-safety probe", is_test=True)
gmail_result = email_sender.GmailSender().send(
    EmailMessage(to=["proof@supplier-a.example.com"], subject="layer3 probe", body="x"))
check("mail.layer3_gmail_no_creds",
      gmail_result.status == "error" and "credentials" in (gmail_result.error or ""),
      f"Gmail path -> status={gmail_result.status!r} error={gmail_result.error!r}")
os.environ["NOTIFICATIONS_V1"] = "1"
mail_provider._OVERRIDE_PROVIDER = saved_override

# Layer 4: governance blocks any non-allowlisted domain BEFORE any transport.
gov_result = email_sender.GmailSender().send(
    EmailMessage(to=["someone@not-allowlisted-real-company.com"],
                 subject="layer4 probe", body="x"))
check("mail.layer4_governance_blocks",
      gov_result.status == "not_allowlisted",
      f"non-allowlisted send -> status={gov_result.status!r} ({gov_result.error!r})")

# Positive path: an allowlisted send lands ONLY in the FakeProvider outbox.
before = len(provider.outbox)
ok_result = email_sender.GmailSender().send(
    EmailMessage(to=["proof@supplier-a.example.com"], subject="capture probe",
                 body="captured, not sent"))
check("mail.capture_works",
      ok_result.status == "sent" and len(provider.outbox) == before + 1
      and provider.outbox[-1]["provider_message_id"].startswith("fake-"),
      f"allowlisted send -> status={ok_result.status!r}, outbox +1, "
      f"provider_message_id={provider.outbox[-1]['provider_message_id'][:10]}...")

# POST-HARDENING (arc 5 T7): with SES_CONFIGURATION_SET_AUTH unset, auth mail
# must still be CAPTURED by FakeProvider (no tracking domain to leak through),
# while the SES provider still refuses — and raises one ACTION_NOW alert.
_saved_auth_set = os.environ.pop("SES_CONFIGURATION_SET_AUTH", None)
try:
    auth_msg = EmailMessage(to=["probe@authmail-probe.test"],
                            subject="t7 auth-mail probe", body="magic link body",
                            metadata={"auth_mail": True})
    cs, refusal = mail_provider.message_configuration_set(auth_msg)
    before_auth = len(provider.outbox)
    fake_auth = provider.send(auth_msg)
    check("hardening.F03_fake_captures_auth_mail_without_set",
          fake_auth.status == "sent" and len(provider.outbox) == before_auth + 1
          and cs is None and refusal is not None,
          f"helper reports refusal ({bool(refusal)}), FakeProvider still captures "
          f"-> status={fake_auth.status!r}, outbox +1")
    ses_auth = mail_provider.SesProvider().send(auth_msg)
    from utils import notifications_store as _ns0
    _auth_alerts = [a for a in _ns0.list_alerts(status=None)
                    if a.get("kind") == "AUTH_MAIL_REFUSED"]
    check("hardening.F03_ses_still_refuses_and_alerts",
          ses_auth.status != "sent" and len(_auth_alerts) >= 1
          and _auth_alerts[0].get("tier") == _ns0.TIER_ACTION_NOW,
          f"SesProvider -> status={ses_auth.status!r} error={ses_auth.error!r}; "
          f"AUTH_MAIL_REFUSED alerts={len(_auth_alerts)} "
          f"tier={_auth_alerts[0].get('tier') if _auth_alerts else None}")
finally:
    if _saved_auth_set is not None:
        os.environ["SES_CONFIGURATION_SET_AUTH"] = _saved_auth_set

# Layer 0 (whole-process): the DNS guard refuses every non-model/search host.
import socket as _socket
try:
    _socket.getaddrinfo("gmail.googleapis.com", 443)
    guard_ok = False
except RuntimeError as exc:
    guard_ok = "EVAL NETWORK GUARD" in str(exc)
check("mail.layer0_network_guard", guard_ok,
      "DNS for gmail.googleapis.com refused by guard (mail infra unreachable)")
check("mail.no_mail_dns_attempted",
      all(h in ("gmail.googleapis.com",) for h in harness.blocked_hosts),
      f"blocked DNS so far: {sorted(set(harness.blocked_hosts))} (only our probe)")

# ---------------------------------------------------------------------------
# 0.3 Mode decision
# ---------------------------------------------------------------------------
live_ok = bool(os.environ.get("ANTHROPIC_API_KEY")) and bool(os.environ.get("TAVILY_API_KEY"))
check("mode.live_keys_present", live_ok,
      "ANTHROPIC_API_KEY and TAVILY_API_KEY present in env (values not shown) -> LIVE mode")
results["mode"] = "LIVE" if live_ok else "REPLAY"

# ---------------------------------------------------------------------------
# 0.5 Seed — buyer + suppliers + accounts + governance allowlist
# ---------------------------------------------------------------------------
from utils import intake_channels, supplier_accounts

# Buyer: tenant 'bayfoods' -> fac-stockton (Bay Foods · Stockton, CA) is in-code.
ks = intake_channels.add_known_sender("bayfoods", "maintenance@bayfoods.com",
                                      is_test=True)
check("seed.known_sender", ks is True,
      "intake known sender maintenance@bayfoods.com -> tenant bayfoods "
      "(fac-stockton, CA). NOTE: no admin API exists for this; seeded at store level.")

# Registry copy: confirm DXP is present and Tier-1 onboarded.
rec = supplier_registry.lookup_by_domain("dxpe.com")
check("seed.registry_dxp",
      bool(rec) and rec.get("tier1_lifecycle") == "onboarded",
      f"registry COPY has DXP: name={rec.get('name') if rec else None}, "
      f"tier1_lifecycle={rec.get('tier1_lifecycle') if rec else None}, "
      f"410-supplier registry copied to eval/e2e/data/")

# Supplier accounts: DXP with OWNER + ADMIN + 2 MEMBERs (S1 routing exercise).
DXP_MEMBERS = [
    ("owner@dxpe.com", "OWNER"),
    ("admin@dxpe.com", "ADMIN"),
    ("member1@dxpe.com", "MEMBER"),
    ("member2@dxpe.com", "MEMBER"),
]
acct = supplier_accounts.create_account("dxpe.com", created_by="eval-phase0")
seeded_members = []
for email, role in DXP_MEMBERS:
    m = supplier_accounts.add_member(
        acct["id"], email, role=getattr(supplier_accounts, f"ROLE_{role}"),
        status=supplier_accounts.MEMBER_ACTIVE, invited_by="eval-phase0")
    seeded_members.append({"email": email, "role": role, "ok": m is not None})
check("seed.dxp_account", acct is not None and all(x["ok"] for x in seeded_members),
      f"dxpe.com account with members {[(x['email'], x['role']) for x in seeded_members]}")

# S4 suppliers: three synthetic accounts (RFC 2606-adjacent .example.com).
s4 = {}
for dom, tz in (("supplier-a.example.com", "America/Los_Angeles"),
                ("supplier-b.example.com", "America/Los_Angeles"),
                ("supplier-c.example.com", "America/Los_Angeles")):
    a = supplier_accounts.create_account(dom, created_by="eval-phase0")
    m = supplier_accounts.add_member(a["id"], f"owner@{dom}",
                                     role=supplier_accounts.ROLE_OWNER,
                                     status=supplier_accounts.MEMBER_ACTIVE,
                                     invited_by="eval-phase0")
    supplier_accounts.set_account_timezone(a["id"], tz)
    s4[dom] = {"account_id": a["id"], "owner": m is not None}
check("seed.s4_accounts", all(v["owner"] for v in s4.values()),
      f"S4 supplier accounts: {list(s4)} (owner-only, tz America/Los_Angeles)")

# Governance allowlist: ONLY the scenario domains (all mail is FakeProvider-
# captured anyway; this is defence-in-depth, not the only layer).
for dom in ("dxpe.com", "supplier-a.example.com", "supplier-b.example.com",
            "supplier-c.example.com", "bayfoods.com"):
    send_governance.allowlist_add(dom, added_by="eval-phase0",
                                  note="e2e eval scenario domain", is_test=True)
al = {r["domain"] for r in send_governance.allowlist_list()}
check("seed.allowlist", al == {"dxpe.com", "supplier-a.example.com",
                               "supplier-b.example.com", "supplier-c.example.com",
                               "bayfoods.com"},
      f"governance allowlist restricted to: {sorted(al)}")

# ---------------------------------------------------------------------------
# Wrap up
# ---------------------------------------------------------------------------
results["network"] = harness.network_summary()
results["data_dir"] = harness.DATA
results["fake_outbox_size"] = len(provider.outbox)
harness.save_evidence("phase0_results.json", results)
print(f"\nEvidence: eval/e2e/evidence/phase0_results.json")
print(f"External calls so far: {results['network']['external_call_count']}")
if FAILED:
    print("PHASE 0 FAILED — see checks above")
    sys.exit(1)
print("PHASE 0 COMPLETE — all checks passed")
