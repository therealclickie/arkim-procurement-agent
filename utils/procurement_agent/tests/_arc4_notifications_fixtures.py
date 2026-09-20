"""
Arc 4 — shared fixtures for the notifications / delivery-tracking tests.

Why a helper module rather than ``conftest.py``: the arc's prime directive is
"no pre-existing test file modified", and ``conftest.py`` is one. This mirrors
arc 3's ``_arc3_session_fixtures`` and the older ``_dsn_fixtures`` /
``_reply_fixtures`` convention — the leading underscore keeps pytest from
collecting it as a test module while the fixtures below are importable into
any arc-4 test file's namespace.

TWO MECHANICAL FACTS these fixtures exist to encode
---------------------------------------------------

1. ``NOTIFICATIONS_V1`` is NOT in ``conftest.py``'s ``_FEATURE_FLAG_ENVS`` pin
   list and cannot be added there without editing a pre-existing test file
   (gate FINDING F4). So every arc-4 fixture pins it EXPLICITLY — off by
   default, on only where a test asks — and the flag is read live
   (``notifications.notifications_active()``), never bound at import, so a
   ``monkeypatch.setenv`` always wins.

2. **No test may reach AWS** (D10). ``isolate_notification_stores`` points
   every store at ``tmp_path`` and leaves the mail provider unselected; a test
   that wants a provider either installs ``FakeProvider`` or drives
   ``SesProvider`` through a botocore ``Stubber``. There is no code path here
   that constructs a live boto3 client.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

ADMIN_TOKEN = "test-admin-secret-arc4"
HTTPS_BASE = "https://testserver"
APP_ORIGIN = "http://localhost:3000"

NOTIFY_SET = "gofer-notifications-test"
AUTH_SET = "gofer-auth-test"
TOPIC_ARN = "arn:aws:sns:us-east-1:123456789012:gofer-ses-events"
FOREIGN_TOPIC_ARN = "arn:aws:sns:us-east-1:999999999999:someone-elses-topic"


def isolate_notification_stores(tmp_path, monkeypatch, *, notifications_on: bool = True):
    """Point every store arc 4 touches at ``tmp_path`` and set the flags.

    Same stores, same order as arc 3's ``_isolate_stores`` (so a notification
    test and a session test differ only in what they assert), plus arc 4's own
    ``notifications_store`` and its SES config.
    """
    from utils import (claim_tokens, notifications_store, quote_store, quote_tokens,
                       send_governance, supplier_accounts, supplier_registry)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("TAVILY_API_KEY", "")
    for mod, name in ((supplier_registry, "supplier_registry.sqlite"),
                      (claim_tokens, "claim_tokens.sqlite"),
                      (supplier_accounts, "supplier_accounts.sqlite"),
                      (send_governance, "send_governance.sqlite"),
                      (notifications_store, "notifications.sqlite")):
        monkeypatch.setattr(mod, "_DATA_DIR", str(tmp_path))
        monkeypatch.setattr(mod, "_DB_PATH", str(tmp_path / name))
    monkeypatch.setattr(quote_store, "_DB_PATH", str(tmp_path / "quotes.sqlite"))
    monkeypatch.setattr(quote_tokens, "_DB_PATH", str(tmp_path / "quote_tokens.sqlite"))

    monkeypatch.setenv("SUPPLIER_ACCOUNTS_V1", "1")
    monkeypatch.setenv("NOTIFICATIONS_V1", "1" if notifications_on else "")
    monkeypatch.setenv("SES_CONFIGURATION_SET_NOTIFICATIONS", NOTIFY_SET)
    monkeypatch.setenv("SES_CONFIGURATION_SET_AUTH", AUTH_SET)
    monkeypatch.setenv("SES_SNS_TOPIC_ARN_ALLOWLIST", TOPIC_ARN)
    monkeypatch.setenv("SES_FROM_ADDRESS", "procurement@arkim.ai")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    return notifications_store


def install_fake_provider(monkeypatch):
    """Select ``FakeProvider`` as the mail transport and return it, so a test
    can read the outbox. Also opens the ``EMAIL_SEND_ENABLED`` delivery gate —
    which is pinned OFF by ``conftest``'s autouse fixture, and which sits
    ABOVE the provider, so without this no provider is ever reached."""
    from utils import email_sender, mail_provider
    provider = mail_provider.FakeProvider()
    monkeypatch.setattr(mail_provider, "_OVERRIDE_PROVIDER", provider)
    monkeypatch.setattr(email_sender, "EMAIL_SEND_ENABLED", True)
    return provider


def allowlist(*domains: str) -> None:
    """Allowlist domains on the REAL governance store (D1: governance tests
    must exercise the real gate, never a mock — reviewer R5)."""
    from utils import send_governance
    for dom in domains:
        send_governance.allowlist_add(dom, added_by="arc4-test", is_test=True)


@pytest.fixture
def notif_stores(tmp_path, monkeypatch):
    """Stores isolated, ``NOTIFICATIONS_V1`` ON, no provider installed."""
    return isolate_notification_stores(tmp_path, monkeypatch)


@pytest.fixture
def notif_api(tmp_path, monkeypatch):
    """HTTPS ``TestClient`` with arc 4's flags on — arc 3's cookie posture
    (the session cookie is ``Secure``, so only an https client returns it)."""
    from utils.procurement_agent.state import persistence
    engine = persistence._make_engine(f"sqlite:///{tmp_path / 'api.sqlite'}")
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    persistence.Base.metadata.create_all(engine)
    monkeypatch.setattr(persistence, "_engine", engine)
    monkeypatch.setattr(persistence, "_SessionFactory", TestSession)

    isolate_notification_stores(tmp_path, monkeypatch)
    monkeypatch.setenv("ARKIM_ADMIN_TOKEN", ADMIN_TOKEN)

    import api_server
    from utils import email_sender, notifications_store, supplier_accounts
    monkeypatch.setattr(api_server, "_engine", engine)
    monkeypatch.setattr(api_server, "_SessionFactory", TestSession)
    monkeypatch.setattr(api_server, "_messages", {})
    monkeypatch.setattr(api_server, "DEMO_MODE", False)
    monkeypatch.setattr(api_server, "_supplier_auth_rate_buckets", {})

    client = TestClient(api_server.app, base_url=HTTPS_BASE)
    client._api_server = api_server
    client._sa = supplier_accounts
    client._store = notifications_store
    client._email_sender = email_sender
    return client


def active_member(client, domain="dxpe.com", email="sales@dxpe.com", role=None):
    """An account with one ACTIVE member (OWNER unless told otherwise)."""
    acct = client._sa.create_account(domain)
    assert acct is not None
    m = client._sa.add_member(acct["id"], email,
                              role=role or client._sa.ROLE_OWNER,
                              status=client._sa.MEMBER_ACTIVE)
    assert m is not None
    return acct, m


def login(client, monkeypatch, email="sales@dxpe.com"):
    """Complete a magic-link login; afterwards every call is cookie-authed.

    Reads the raw token out of the DELIVERED message body — never out of the
    response (there is none) and never out of the store (only the hash is
    there), exactly as arc 3 does.
    """
    recorded = []
    email_sender = client._email_sender

    class RecordingSender(email_sender.GmailSender):
        def send(self, message):
            recorded.append(message)
            return super().send(message)

    monkeypatch.setattr(email_sender, "GmailSender", RecordingSender)
    r = client.post("/api/supplier/auth/request-link", json={"email": email})
    assert r.status_code == 200, r.text
    assert recorded, "no magic-link email was delivered"
    token = recorded[0].body.split("/supplier/verify?token=", 1)[1].split("\n", 1)[0].strip()
    r = client.post("/api/supplier/auth/verify", json={"token": token},
                    headers={"Origin": APP_ORIGIN})
    assert r.status_code == 200, r.text
    return r


def admin_headers() -> dict:
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def open_rfq(domain="dxpe.com", run_id="run-1", *, status="sent",
             part_key="acme|p-1") -> str:
    """One ``sent_messages`` row in an OPEN status — the row the portal inbox
    renders and therefore the subject an ``RFQ_NEW`` notification refers to
    (the Q1 ruling: ``rfq_send`` owns RFQ_NEW, so a notification's subject is
    always something the supplier can actually view)."""
    from utils import supplier_registry
    rid = supplier_registry.record_sent_message(
        run_id=run_id, supplier_domain=domain, vendor_name="DXP",
        to=[f"sales@{domain}"], cc=[], subject="Quote request",
        body="body", status=status, part_key=part_key)
    assert rid is not None
    return rid


def hours_ago(hours: float) -> str:
    """An ISO-8601 UTC timestamp ``hours`` in the past."""
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


# ---------------------------------------------------------------------------
# SNS envelope helpers (T6) — a REAL signature over a REAL self-signed cert,
# so the verification code is exercised rather than stubbed out.
# ---------------------------------------------------------------------------

def make_signing_key_and_cert():
    """A throwaway RSA key + self-signed X.509 certificate, generated in the
    test process. ``cryptography`` is already a runtime dependency (via
    ``python-jose[cryptography]``) — no new test dependency."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "sns.amazonaws.com")])
    cert = (x509.CertificateBuilder()
            .subject_name(subject).issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
            .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))
            .sign(key, hashes.SHA256()))
    pem = cert.public_bytes(serialization.Encoding.PEM)
    return key, pem


def sign_envelope(envelope: dict, key) -> dict:
    """Sign an SNS envelope the way SNS does (SignatureVersion 1: SHA1 over
    the canonical field string) and return it with ``Signature`` filled in."""
    import base64
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from utils.ses_webhook import canonical_string_to_sign

    body = canonical_string_to_sign(envelope)
    sig = key.sign(body.encode("utf-8"), padding.PKCS1v15(), hashes.SHA1())
    return {**envelope, "Signature": base64.b64encode(sig).decode()}


def ses_event_envelope(key, *, event_type: str, message_id: str,
                       topic_arn: str = TOPIC_ARN,
                       extra: Optional[dict] = None) -> dict:
    """A signed SNS ``Notification`` envelope carrying one SES event."""
    ses_event = {
        "eventType": event_type,
        "mail": {"messageId": message_id, "timestamp": "2026-09-20T10:00:00.000Z"},
    }
    if extra:
        ses_event.update(extra)
    return sign_envelope({
        "Type": "Notification",
        "MessageId": f"sns-{message_id}-{event_type}",
        "TopicArn": topic_arn,
        "Subject": "Amazon SES Email Event Notification",
        "Message": json.dumps(ses_event),
        "Timestamp": "2026-09-20T10:00:01.000Z",
        "SignatureVersion": "1",
        "SigningCertURL": "https://sns.us-east-1.amazonaws.com/cert.pem",
    }, key)


def subscription_confirmation(key, *, topic_arn: str = TOPIC_ARN,
                              subscribe_url: str = "https://sns.us-east-1.amazonaws.com/?Action=ConfirmSubscription"
                              ) -> dict:
    """A signed SNS ``SubscriptionConfirmation`` envelope."""
    return sign_envelope({
        "Type": "SubscriptionConfirmation",
        "MessageId": "sns-confirm-1",
        "TopicArn": topic_arn,
        "Token": "confirm-token",
        "Message": "You have chosen to subscribe to the topic",
        "SubscribeURL": subscribe_url,
        "Timestamp": "2026-09-20T10:00:01.000Z",
        "SignatureVersion": "1",
        "SigningCertURL": "https://sns.us-east-1.amazonaws.com/cert.pem",
    }, key)
