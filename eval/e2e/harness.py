"""Shared isolation + safety harness for the flags-on E2E evaluation.

EVALUATION CODE ONLY — lives under eval/e2e/, never imported by the app.

Guarantees enforced here (Phase 0, brief rules 3-6):
  * Pilot flag profile installed in os.environ BEFORE any repo import, so
    import-bound flags (TIER1_V2, SCORING_V2, SUPPLIER_PORTAL_V1,
    CLAIM_TOKENS_ENABLED, RUN_CAPTURE, EMAIL_SEND_ENABLED) bind ON correctly.
  * Every persistent store is repointed at eval/e2e/data/ (fresh or a COPY of
    the developer seed) — the developer's data/ is never written.
  * Outbound mail transport is a single shared FakeProvider instance
    (mail_provider._OVERRIDE_PROVIDER), so every delivered mail is captured
    in-memory with its body. Gmail creds and AWS_REGION are blanked so the
    real transports cannot construct.
  * A DNS-level network guard (socket.getaddrinfo) refuses every host except
    api.anthropic.com / api.tavily.com / loopback. Any attempt to reach mail
    or AWS infrastructure raises and is recorded.
  * Every external HTTP call (httpx + requests) is counted; the harness hard
    stops the run at MAX_EXTERNAL_CALLS (brief rule 6: 150).

Usage (from a driver script under eval/e2e/):
    import harness
    harness.install(live=True)          # env + guard + counter; BEFORE repo imports
    harness.isolate_stores(fresh=True)  # repoint stores; copies seeds
    client, api_server = harness.make_client()
    provider = harness.fake_provider()
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DATA = os.path.join(HERE, "data")
EVIDENCE = os.path.join(HERE, "evidence")
DEV_DATA = os.path.join(ROOT, "data")

ADMIN_TOKEN = "eval-admin-token-e2e"
MAX_EXTERNAL_CALLS = 150

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ---------------------------------------------------------------------------
# PILOT FLAG PROFILE (Phase 0.1 deliverable — mirrored in the report)
# ---------------------------------------------------------------------------
# Backend feature flags: everything a pilot needs ON. DEMO_MODE stays OFF —
# it is the public no-login demo spine: it 403s non-demo routes, forces
# Apollo off, and REFUSES TO BOOT with EMAIL_SEND_ENABLED, so it is
# structurally incompatible with the pilot profile.
PILOT_FLAGS = {
    "TIER1_V2": "1",
    "RANKING_BANDS_V1": "1",
    "SUPPLIER_PORTAL_V1": "1",       # also binds claim_tokens.CLAIM_TOKENS_ENABLED
    "SEND_GOVERNANCE_V1": "1",
    "INTAKE_CHANNELS_V1": "1",
    "INTAKE_TYPE_AWARE": "1",
    "SCORING_V2": "1",
    "RUN_CAPTURE": "1",
    "QUOTE_SUBMIT_V1": "1",
    "SUPPLIER_ACCOUNTS_V1": "1",
    "NOTIFICATIONS_V1": "1",
    "DEMO_MODE": "",                 # OFF (incompatible with mail-capture pilot)
}

# Mail-safety configuration (brief rule 3). EMAIL_SEND_ENABLED must be ON to
# reach ANY transport (the stub gate sits above provider selection in
# email_sender.send), so safety rests on the four layers below, each proven
# by eval/e2e/phase0_mail_safety.py.
MAIL_SAFETY = {
    "EMAIL_SEND_ENABLED": "1",       # opens the delivery gate to the FAKE transport
    "MAIL_PROVIDER": "fake",         # transport selection: FakeProvider (in-memory)
    "GMAIL_SERVICE_ACCOUNT_JSON": "",  # Gmail transport cannot construct
    "GMAIL_SERVICE_ACCOUNT_FILE": "",
    "GMAIL_OAUTH_TOKEN_FILE": "",
    "AWS_REGION": "",                # SES transport cannot construct (no region)
    "SES_CONFIGURATION_SET_NOTIFICATIONS": "eval-notify-set",
    "SES_CONFIGURATION_SET_AUTH": "eval-auth-set",  # unset => auth mail refused
    "SES_FROM_ADDRESS": "procurement@arkim.ai",
}

# Isolation (rule 5). POST-HARDENING: arc 5 T10 (R10, F-04) introduced
# GOFER_DATA_DIR — set it BEFORE any repo import so every store, persistence,
# and the two utils/ JSON stores resolve natively under eval/e2e/data/.
# This run uses the new mechanism as the PRIMARY isolation (a live exercise of
# the F-04 fix); isolate_stores() verifies it and records whether any module
# still needed the old monkeypatch.
ISOLATION = {
    "GOFER_DATA_DIR": DATA,
}

# External-spend controls for the eval (LIVE = Anthropic + Tavily only).
SPEND_CONTROLS = {
    "APOLLO_API_KEY": "",            # no Apollo credits burned in this eval
    "PARALLEL_API_KEY": "",
    "LANGSMITH_TRACING": "false",    # no telemetry egress (network guard would block)
    "LANGSMITH_API_KEY": "",
    "ARKIM_ADMIN_TOKEN": ADMIN_TOKEN,
    # Link bases used inside outbound mail bodies (quote links, magic links).
    "ARKIM_PUBLIC_BASE_URL": "http://localhost:3000",
    "SUPPLIER_PORTAL_BASE_URL": "http://localhost:3000",
}

# Frontend flags (recorded for the report / Phase 3; not used by the API driver).
FRONTEND_FLAGS = {
    "NEXT_PUBLIC_API_URL": "http://localhost:8001",  # arc 5 T9 (F-02): default in next.config.ts is now :8001 — verified in phase 0
    "NEXT_PUBLIC_SUPPLIER_SESSION_V1": "1",
    "NEXT_PUBLIC_NOTIFICATIONS_V1": "1",
}

# ---------------------------------------------------------------------------
# Network guard + external-call counter
# ---------------------------------------------------------------------------
ALLOWED_HOSTS = {"api.anthropic.com", "api.tavily.com",
                 "localhost", "127.0.0.1", "::1", "testserver"}
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "testserver"}

external_calls: list[str] = []      # "host path" per outbound HTTP request
blocked_hosts: list[str] = []       # DNS lookups refused by the guard

_installed = False


class ExternalCallBudgetExceeded(RuntimeError):
    pass


def _install_network_guard() -> None:
    real = socket.getaddrinfo

    def guarded(host, *args, **kwargs):
        name = str(host or "").strip("[]").lower()
        if name not in ALLOWED_HOSTS:
            blocked_hosts.append(name)
            raise RuntimeError(
                f"[EVAL NETWORK GUARD] refused DNS resolution for {name!r} — "
                f"only {sorted(ALLOWED_HOSTS - _LOCAL_HOSTS)} are permitted")
        return real(host, *args, **kwargs)

    socket.getaddrinfo = guarded


def _count(host: str | None, path: str) -> None:
    h = (host or "").lower()
    if h in _LOCAL_HOSTS or not h:
        return
    external_calls.append(f"{h} {path}")
    if len(external_calls) > MAX_EXTERNAL_CALLS:
        raise ExternalCallBudgetExceeded(
            f"external call budget ({MAX_EXTERNAL_CALLS}) exceeded")


def _install_call_counter() -> None:
    try:
        import httpx
        _orig_send = httpx.Client.send

        def counted_send(self, request, *a, **k):
            _count(request.url.host, str(request.url.path))
            return _orig_send(self, request, *a, **k)

        httpx.Client.send = counted_send

        _orig_async = httpx.AsyncClient.send

        async def counted_async(self, request, *a, **k):
            _count(request.url.host, str(request.url.path))
            return await _orig_async(self, request, *a, **k)

        httpx.AsyncClient.send = counted_async
    except ImportError:
        pass
    try:
        import requests
        from urllib.parse import urlparse
        _orig_req = requests.Session.send

        def counted_req(self, prepared, *a, **k):
            u = urlparse(prepared.url or "")
            _count(u.hostname, u.path or "")
            return _orig_req(self, prepared, *a, **k)

        requests.Session.send = counted_req
    except ImportError:
        pass


def install(live: bool = True) -> None:
    """Env profile + guard + counter. Call BEFORE any repo import."""
    global _installed
    if _installed:
        return
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))   # brings the model/search keys
    for group in (PILOT_FLAGS, MAIL_SAFETY, ISOLATION, SPEND_CONTROLS):
        os.environ.update(group)
    if not live:
        os.environ["ANTHROPIC_API_KEY"] = ""
        os.environ["TAVILY_API_KEY"] = ""
    _install_network_guard()
    _install_call_counter()
    os.makedirs(EVIDENCE, exist_ok=True)
    _installed = True


# ---------------------------------------------------------------------------
# Store isolation (rule 5)
# ---------------------------------------------------------------------------
# Seeds copied from the developer data so the demo-tomorrow reality (registry
# incl. DXP, LLM caches, known-parts, price cache) is preserved; everything
# else starts fresh. The developer files are opened read-only (copy) and
# never written.
_COPY_SEEDS = [
    ("data/supplier_registry.sqlite", "supplier_registry.sqlite"),
    ("data/brand_intelligence.sqlite", "brand_intelligence.sqlite"),
    ("data/spec_cache.sqlite", "spec_cache.sqlite"),
    ("data/mock_maintenance_handoffs.json", "mock_maintenance_handoffs.json"),
    ("utils/known_parts.json", "known_parts.json"),
    ("utils/price_db.json", "price_db.json"),
]

# (module, filename) pairs whose _DATA_DIR/_DB_PATH get repointed at DATA.
_STORE_MODULES = [
    ("utils.audit_log", "audit_log.sqlite"),
    ("utils.brand_intelligence", "brand_intelligence.sqlite"),
    ("utils.claim_tokens", "claim_tokens.sqlite"),
    ("utils.intake_channels", "intake_channels.sqlite"),
    ("utils.notifications_store", "notifications.sqlite"),
    ("utils.orders", "orders.sqlite"),
    ("utils.quote_store", "quotes.sqlite"),
    ("utils.quote_tokens", "quote_tokens.sqlite"),
    ("utils.run_capture", "run_capture.sqlite"),
    ("utils.run_labels", "run_labels.sqlite"),
    ("utils.send_governance", "send_governance.sqlite"),
    ("utils.site_settings", "site_settings.sqlite"),
    ("utils.spec_lookup", "spec_cache.sqlite"),
    ("utils.supplier_accounts", "supplier_accounts.sqlite"),
    ("utils.supplier_registry", "supplier_registry.sqlite"),
]


# Filled by isolate_stores(): per-module record of whether GOFER_DATA_DIR
# (arc 5 T10) natively isolated it, before any monkeypatch was applied.
t10_native_isolation: dict = {}


def isolate_stores(fresh: bool = False) -> str:
    """Isolate every store under eval/e2e/data/. fresh=True wipes it first.

    POST-HARDENING: GOFER_DATA_DIR (set in install()) should make every module
    resolve here natively. We record what each module resolved to BEFORE
    patching (T10 evidence), then still apply the old monkeypatch as
    belt-and-braces — a no-op when T10 works.
    """
    import importlib
    if fresh and os.path.isdir(DATA):
        shutil.rmtree(DATA)
    os.makedirs(DATA, exist_ok=True)
    for src_rel, dst_name in _COPY_SEEDS:
        src = os.path.join(ROOT, *src_rel.split("/"))
        dst = os.path.join(DATA, dst_name)
        if os.path.exists(src) and not os.path.exists(dst):
            shutil.copyfile(src, dst)
    for mod_name, fname in _STORE_MODULES:
        mod = importlib.import_module(mod_name)
        native = str(getattr(mod, "_DB_PATH", ""))
        t10_native_isolation[mod_name] = {
            "native_db_path": native,
            "under_eval_data": native.startswith(DATA)}
        if hasattr(mod, "_DATA_DIR"):
            mod._DATA_DIR = DATA
        mod._DB_PATH = os.path.join(DATA, fname)
    # price_db + known_parts historically lived in utils/; under T10 they
    # follow GOFER_DATA_DIR. Record native paths, then pin to the copies.
    import utils.known_parts as kp
    import utils.price_db as pdb
    for label, mod in (("utils.known_parts", kp), ("utils.price_db", pdb)):
        native = str(getattr(mod, "_DB_PATH", ""))
        t10_native_isolation[label] = {
            "native_db_path": native,
            "under_eval_data": native.startswith(DATA)}
    kp._DB_PATH = os.path.join(DATA, "known_parts.json")
    pdb._DB_PATH = os.path.join(DATA, "price_db.json")
    # sourcing-runs engine: isolated DB, BEFORE api_server import.
    from utils.procurement_agent.state import persistence
    from sqlalchemy.orm import sessionmaker
    db_path = os.path.join(DATA, "sourcing_runs.sqlite")
    native = str(getattr(persistence, "_DB_PATH", ""))
    t10_native_isolation["persistence"] = {
        "native_db_path": native, "under_eval_data": native.startswith(DATA)}
    persistence._DB_PATH = db_path
    engine = persistence._make_engine(f"sqlite:///{db_path}")
    persistence.Base.metadata.create_all(engine)
    persistence._engine = engine
    persistence._SessionFactory = sessionmaker(bind=engine,
                                               expire_on_commit=False)
    return DATA


def make_client(base_url: str = "https://testserver"):
    """Import api_server AFTER env+isolation; return (TestClient, api_server)."""
    from fastapi.testclient import TestClient
    from utils.procurement_agent.state import persistence
    import api_server
    api_server._engine = persistence._engine
    api_server._SessionFactory = persistence._SessionFactory
    client = TestClient(api_server.app, base_url=base_url)
    return client, api_server


_provider = None


def fake_provider():
    """Install (once) and return the SHARED FakeProvider capture instance."""
    global _provider
    from utils import email_sender, mail_provider
    if _provider is None:
        _provider = mail_provider.FakeProvider()
    mail_provider._OVERRIDE_PROVIDER = _provider
    email_sender.EMAIL_SEND_ENABLED = True   # bound at import from env anyway
    return _provider


def admin_headers() -> dict:
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


# ---------------------------------------------------------------------------
# Evidence helpers
# ---------------------------------------------------------------------------

def save_evidence(name: str, payload) -> str:
    os.makedirs(EVIDENCE, exist_ok=True)
    path = os.path.join(EVIDENCE, name)
    with open(path, "w", encoding="utf-8") as f:
        if isinstance(payload, str):
            f.write(payload)
        else:
            json.dump(payload, f, indent=2, default=str)
    return path


def redact_outbox(outbox: list[dict]) -> list[dict]:
    """Outbox snapshot safe for evidence: bodies kept (they contain tokens we
    used — single-use, isolated store, never leaves the repo), plus counts."""
    return outbox


def network_summary() -> dict:
    from collections import Counter
    return {
        "external_call_count": len(external_calls),
        "budget": MAX_EXTERNAL_CALLS,
        "by_host": dict(Counter(c.split(" ", 1)[0] for c in external_calls)),
        "blocked_dns_lookups": sorted(set(blocked_hosts)),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
