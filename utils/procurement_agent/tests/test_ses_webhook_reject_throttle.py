"""
Arc 4b T2 / R-F10 — the webhook throttles its REJECTION path only.

WHY NOT A PLAIN PER-IP LIMITER
------------------------------
SNS delivers from AWS ranges, so a bucket over ALL webhook traffic risks
dropping a legitimate burst — and **a dropped verified event is permanent data
loss**: the SNS retry policy is finite, and a missed Delivery/Bounce leaves a
notification stuck in the wrong state forever, which is precisely the tracking
failure the notification surface exists to prevent.

So only requests that FAIL the topic allowlist or the signature check are
counted, and ``test_a_verified_event_from_a_throttled_ip_still_succeeds`` is
the load-bearing test in this file (reviewer R4).

WHY THE ASSERTIONS ARE AT THE LIMITER SEAM, NOT ON THE RESPONSE
---------------------------------------------------------------
The throttled answer is byte-identical to every other rejection (reviewer R5):
the same bare ``HTTPException(403, "Forbidden")``, no ``Retry-After``, no extra
header. That is deliberate — a limiter whose response differs is an oracle that
tells a prober "your signature was wrong" apart from "you are going too fast".
It therefore CANNOT be observed over HTTP, and is observed instead through
``api_server._webhook_reject_throttled``, a read that counts nothing.

No socket is opened (D10): the one replaced seam is ``fetch_certificate_pem``,
and the signature itself is real.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from utils import notifications_store as ns, ses_webhook
from utils.procurement_agent.tests._arc4_notifications_fixtures import (  # noqa: F401
    HTTPS_BASE, TOPIC_ARN, isolate_notification_stores,
    make_signing_key_and_cert, ses_event_envelope, sign_envelope,
)

WEBHOOK = "/api/webhooks/ses"
IP_A = "203.0.113.9"
IP_B = "198.51.100.4"


def _client(tmp_path, monkeypatch, ip: str, *, name: str = "a"):
    """A TestClient whose ASGI scope carries ``ip`` as the client host, so the
    real ``_client_ip`` keying is exercised rather than monkeypatched."""
    from utils.procurement_agent.state import persistence
    engine = persistence._make_engine(f"sqlite:///{tmp_path / f'api-{name}.sqlite'}")
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    persistence.Base.metadata.create_all(engine)
    monkeypatch.setattr(persistence, "_engine", engine)
    monkeypatch.setattr(persistence, "_SessionFactory", TestSession)

    import api_server
    monkeypatch.setattr(api_server, "_engine", engine)
    monkeypatch.setattr(api_server, "_SessionFactory", TestSession)
    monkeypatch.setattr(api_server, "DEMO_MODE", False)
    return TestClient(api_server.app, base_url=HTTPS_BASE, client=(ip, 50000))


@pytest.fixture
def hook(tmp_path, monkeypatch):
    """Arc-4 stores + flags, a real signing key installed as the certificate
    this process "fetches", and empty rejection buckets."""
    import api_server
    isolate_notification_stores(tmp_path, monkeypatch)
    key, pem = make_signing_key_and_cert()
    monkeypatch.setattr(ses_webhook, "_CERT_CACHE", {})
    monkeypatch.setattr(ses_webhook, "fetch_certificate_pem", lambda url: pem)
    monkeypatch.setattr(api_server, "_webhook_reject_buckets", {})
    return key


def post(client, envelope):
    body = envelope if isinstance(envelope, (str, bytes)) else json.dumps(envelope)
    return client.post(WEBHOOK, content=body,
                       headers={"Content-Type": "text/plain",
                                "x-amz-sns-message-type": "Notification"})


def bad_signature(key, *, message_id: str = "ses-msg-1") -> dict:
    good = ses_event_envelope(key, event_type="Delivery", message_id=message_id)
    return {**good, "Signature": "AAAA"}


def foreign_topic(key) -> dict:
    good = ses_event_envelope(key, event_type="Delivery", message_id="ses-msg-1")
    return sign_envelope({**good, "TopicArn":
                          "arn:aws:sns:us-east-1:999999999999:not-ours"}, key)


def sent_notification(pmid: str) -> dict:
    n = ns.create_notification(kind=ns.KIND_RFQ_NEW, run_id="run-1",
                               supplier_domain="dxpe.com",
                               recipient="sales@dxpe.com", is_test=True)
    assert n is not None
    assert ns.set_provider_message_id(n["id"], pmid)
    ns.transition(n["id"], ns.STATE_SENT, event_type="Send")
    return ns.get_notification(n["id"])


# ---------------------------------------------------------------------------
# The budget
# ---------------------------------------------------------------------------

def test_the_61st_rejection_from_one_ip_trips_the_limiter(hook, tmp_path, monkeypatch):
    """The default budget is 60 rejections per IP per minute. Sixty are within
    it; the sixty-first is over — and the response never says so."""
    import api_server
    client = _client(tmp_path, monkeypatch, IP_A)
    for i in range(60):
        assert post(client, bad_signature(hook)).status_code == 403
        assert api_server._webhook_reject_throttled(IP_A) is False, i
    assert post(client, bad_signature(hook)).status_code == 403
    assert api_server._webhook_reject_throttled(IP_A) is True


def test_a_failed_topic_allowlist_counts_against_the_same_budget(
        hook, tmp_path, monkeypatch):
    """R-F10 names both failure paths: the allowlist AND the signature."""
    import api_server
    monkeypatch.setenv("WEBHOOK_REJECT_RATE_LIMIT", "2")
    client = _client(tmp_path, monkeypatch, IP_A)
    for _ in range(3):
        assert post(client, foreign_topic(hook)).status_code == 403
    assert api_server._webhook_reject_throttled(IP_A) is True


# ---------------------------------------------------------------------------
# THE ONE THAT MATTERS (reviewer R4): a verified event is never throttled
# ---------------------------------------------------------------------------

def test_a_verified_event_from_a_throttled_ip_still_succeeds(
        hook, tmp_path, monkeypatch):
    """A dropped verified event is permanent data loss. Spend the whole
    rejection budget from an IP, then deliver a genuine event from the SAME IP
    in the SAME window: it is accepted and APPLIED."""
    import api_server
    monkeypatch.setenv("WEBHOOK_REJECT_RATE_LIMIT", "2")
    client = _client(tmp_path, monkeypatch, IP_A)
    notification = sent_notification("ses-msg-real")
    for _ in range(5):
        assert post(client, bad_signature(hook)).status_code == 403
    assert api_server._webhook_reject_throttled(IP_A) is True

    r = post(client, ses_event_envelope(hook, event_type="Delivery",
                                        message_id="ses-msg-real"))
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert ns.get_notification(notification["id"])["state"] == ns.STATE_DELIVERED


def test_a_verified_event_does_not_consume_the_rejection_budget(
        hook, tmp_path, monkeypatch):
    """Only failures are counted — a busy, legitimate SNS burst can never
    throttle itself into the rejection bucket."""
    import api_server
    monkeypatch.setenv("WEBHOOK_REJECT_RATE_LIMIT", "1")
    client = _client(tmp_path, monkeypatch, IP_A)
    for i in range(5):
        sent_notification(f"ses-msg-{i}")
        assert post(client, ses_event_envelope(
            hook, event_type="Delivery", message_id=f"ses-msg-{i}")).status_code == 200
    assert api_server._webhook_reject_buckets == {}
    assert api_server._webhook_reject_throttled(IP_A) is False


# ---------------------------------------------------------------------------
# The limiter is not an oracle (reviewer R5)
# ---------------------------------------------------------------------------

def test_the_throttled_403_is_byte_identical_to_every_other_rejection(
        hook, tmp_path, monkeypatch):
    """Status, body bytes and the security headers, compared as a set. Size 1
    or the limiter has told a prober something."""
    import api_server
    monkeypatch.setenv("WEBHOOK_REJECT_RATE_LIMIT", "2")
    client = _client(tmp_path, monkeypatch, IP_A)
    seen = set()
    for _ in range(2):                      # within budget
        r = post(client, bad_signature(hook))
        seen.add((r.status_code, r.content, r.headers.get("cache-control"),
                  r.headers.get("referrer-policy"), r.headers.get("retry-after")))
    assert api_server._webhook_reject_throttled(IP_A) is False
    for body in (bad_signature(hook), foreign_topic(hook), b"{not json", b""):
        r = post(client, body)              # over budget from here on
        seen.add((r.status_code, r.content, r.headers.get("cache-control"),
                  r.headers.get("referrer-policy"), r.headers.get("retry-after")))
    assert api_server._webhook_reject_throttled(IP_A) is True
    assert len(seen) == 1, f"the limiter is distinguishable: {seen}"
    status, content, _, _, retry_after = seen.pop()
    assert status == 403
    assert json.loads(content) == {"detail": "Forbidden"}
    assert retry_after is None, "a Retry-After header would name the limiter"


# ---------------------------------------------------------------------------
# Bucket isolation, configuration, and the flag
# ---------------------------------------------------------------------------

def test_two_ips_have_independent_buckets(hook, tmp_path, monkeypatch):
    import api_server
    monkeypatch.setenv("WEBHOOK_REJECT_RATE_LIMIT", "2")
    a = _client(tmp_path, monkeypatch, IP_A, name="a")
    b = _client(tmp_path, monkeypatch, IP_B, name="b")
    for _ in range(3):
        post(a, bad_signature(hook))
    assert api_server._webhook_reject_throttled(IP_A) is True
    assert api_server._webhook_reject_throttled(IP_B) is False
    assert post(b, bad_signature(hook)).status_code == 403
    assert api_server._webhook_reject_throttled(IP_B) is False


def test_a_cap_of_zero_switches_the_limiter_off(hook, tmp_path, monkeypatch):
    """The house convention for an inert limiter."""
    import api_server
    monkeypatch.setenv("WEBHOOK_REJECT_RATE_LIMIT", "0")
    client = _client(tmp_path, monkeypatch, IP_A)
    for _ in range(20):
        assert post(client, bad_signature(hook)).status_code == 403
    assert api_server._webhook_reject_throttled(IP_A) is False
    assert api_server._webhook_reject_buckets == {}


def test_the_limiter_is_never_reached_when_the_flag_is_off(
        tmp_path, monkeypatch):
    """Flag off ⇒ the route never existed: a 404 identical to an unknown path,
    and nothing counted."""
    import api_server
    isolate_notification_stores(tmp_path, monkeypatch, notifications_on=False)
    monkeypatch.setattr(api_server, "_webhook_reject_buckets", {})
    monkeypatch.setenv("WEBHOOK_REJECT_RATE_LIMIT", "1")
    client = _client(tmp_path, monkeypatch, IP_A)
    for _ in range(5):
        r = post(client, b"{not json")
        assert r.status_code == 404
        assert r.json() == {"detail": "Not Found"}
    assert api_server._webhook_reject_buckets == {}
