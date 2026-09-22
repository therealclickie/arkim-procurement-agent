"""
Arc 4 T6 / D4 — the SES → SNS delivery-event webhook.

THE BRIEF'S LIST, MADE FALSIFIABLE
----------------------------------
"valid signed envelope → transition; bad signature → 403; unknown TopicArn →
403; non-amazonaws SigningCertURL → 403; replayed event → no-op; confirmation
only for allowlisted topic; all rejections byte-identical."

TWO THINGS THESE TESTS DO THE HARD WAY, ON PURPOSE
--------------------------------------------------
1. **The signature is real.** ``_arc4_notifications_fixtures`` generates a
   throwaway RSA key and a self-signed certificate in-process and signs each
   envelope the way SNS does, so ``verify_signature`` runs its real
   certificate-parsing and RSA-verification path. A monkeypatched
   "signature ok" would have proved nothing about the code under test.
2. **No socket is ever opened** (D10). Exactly one seam is replaced —
   ``fetch_certificate_pem`` — and the ordering test proves the SubscribeURL
   fetch is never reached for a foreign topic by recording calls, rather than
   by trusting the code's comments.
"""
from __future__ import annotations

import json

import pytest

from utils import notifications_store as ns, ses_webhook
from utils.procurement_agent.tests._arc4_notifications_fixtures import (  # noqa: F401
    FOREIGN_TOPIC_ARN, TOPIC_ARN, isolate_notification_stores,
    make_signing_key_and_cert, notif_api, ses_event_envelope, sign_envelope,
    subscription_confirmation,
)

WEBHOOK = "/api/webhooks/ses"

# Every rejection the brief names, plus the near-miss certificate hosts. The
# labels are the parametrize ids; ``rejection_cases`` builds the bodies.
REJECTION_LABELS = (
    "bad_signature", "tampered_message", "unknown_topic", "non_amazonaws_cert",
    "amazonaws_suffix_trick", "http_cert_url", "unknown_signature_version",
    "unknown_envelope_type", "malformed_json", "empty_body", "json_array",
    "missing_signature",
)


class _FakeResponse:
    """The minimum of ``urlopen``'s contract that
    :func:`fetch_certificate_pem` uses: a context manager with ``read()``."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


@pytest.fixture
def signing_key(monkeypatch):
    """A real key/cert pair, installed as the certificate this process will
    "fetch". The cache is cleared so one test's certificate never leaks into
    the next, and ``fetch_certificate_pem`` is replaced so no test can reach
    the network (D10)."""
    key, pem = make_signing_key_and_cert()
    monkeypatch.setattr(ses_webhook, "_CERT_CACHE", {})
    monkeypatch.setattr(ses_webhook, "fetch_certificate_pem", lambda url: pem)
    return key


@pytest.fixture
def no_subscribe_fetch(monkeypatch):
    """Record every SubscribeURL this process would visit, and visit none of
    them. The list is the evidence for the allowlist-before-fetch ordering."""
    visited: list[str] = []

    def _recorder(url: str) -> bool:
        visited.append(url)
        return True

    monkeypatch.setattr(ses_webhook, "confirm_subscription", _recorder)
    return visited


def sent_notification(*, pmid: str = "ses-msg-1", recipient: str = "sales@dxpe.com",
                      run_id: str = "run-1") -> dict:
    """One notification already handed to the provider: SENT, with the provider
    message id every later SNS event joins on."""
    n = ns.create_notification(kind=ns.KIND_RFQ_NEW, run_id=run_id,
                               supplier_domain="dxpe.com", recipient=recipient,
                               is_test=True)
    assert n is not None
    assert ns.set_provider_message_id(n["id"], pmid)
    ns.transition(n["id"], ns.STATE_SENT, event_type="Send")
    return ns.get_notification(n["id"])


def rejection_cases(key) -> dict:
    """label → the body to POST, for every rejection path."""
    good = ses_event_envelope(key, event_type="Delivery", message_id="ses-msg-1")
    return {
        "bad_signature": {**good, "Signature": "AAAA"},
        "tampered_message": {**good, "Message": json.dumps(
            {"eventType": "Delivery",
             "mail": {"messageId": "ses-msg-1", "timestamp": "x"}})},
        "unknown_topic": sign_envelope({**good, "TopicArn": FOREIGN_TOPIC_ARN}, key),
        "non_amazonaws_cert": sign_envelope(
            {**good, "SigningCertURL": "https://attacker.test/cert.pem"}, key),
        "amazonaws_suffix_trick": sign_envelope(
            {**good,
             "SigningCertURL": "https://sns.amazonaws.com.attacker.test/c.pem"}, key),
        "http_cert_url": sign_envelope(
            {**good, "SigningCertURL": "http://sns.us-east-1.amazonaws.com/c.pem"}, key),
        "unknown_signature_version": sign_envelope(
            {**good, "SignatureVersion": "9"}, key),
        "unknown_envelope_type": sign_envelope({**good, "Type": "SomethingElse"}, key),
        "malformed_json": b"{not json",
        "empty_body": b"",
        "json_array": b"[]",
        "missing_signature": {k: v for k, v in good.items() if k != "Signature"},
    }


def post(client, envelope):
    """POST an envelope the way SNS does — raw body, ``text/plain``."""
    body = envelope if isinstance(envelope, (str, bytes)) else json.dumps(envelope)
    return client.post(WEBHOOK, content=body,
                       headers={"Content-Type": "text/plain",
                                "x-amz-sns-message-type": "Notification"})


# ---------------------------------------------------------------------------
# Flag posture
# ---------------------------------------------------------------------------

def test_flag_off_the_route_does_not_exist(notif_api, monkeypatch, signing_key):
    """Prime directive 2: flag off ⇒ the webhook is a 404 byte-identical to an
    unknown route."""
    monkeypatch.setenv("NOTIFICATIONS_V1", "")
    r = post(notif_api, ses_event_envelope(signing_key, event_type="Delivery",
                                           message_id="ses-msg-1"))
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}
    unknown = notif_api.post("/api/webhooks/does-not-exist", json={})
    assert (r.status_code, r.json()) == (unknown.status_code, unknown.json())


# ---------------------------------------------------------------------------
# The happy path: a signed envelope on an allowlisted topic transitions state
# ---------------------------------------------------------------------------

def test_valid_signed_envelope_transitions_the_notification(notif_api, signing_key):
    n = sent_notification()
    r = post(notif_api, ses_event_envelope(signing_key, event_type="Delivery",
                                           message_id="ses-msg-1"))
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}
    after = ns.get_notification(n["id"])
    assert after["state"] == ns.STATE_DELIVERED
    assert after["delivered_at"]


@pytest.mark.parametrize("event_type,expected", [
    ("Delivery", ns.STATE_DELIVERED),
    ("Open", ns.STATE_OPENED),
    ("Click", ns.STATE_CLICKED),
    ("Bounce", ns.STATE_BOUNCED),
    ("Complaint", ns.STATE_COMPLAINED),
    ("Reject", ns.STATE_REJECTED),
])
def test_d4_event_mapping(notif_api, signing_key, event_type, expected):
    """D4's mapping table, end to end through the real webhook."""
    n = sent_notification(pmid=f"ses-{event_type}")
    extra = {"bounce": {"bounceType": "Permanent", "bounceSubType": "General",
                        "bouncedRecipients": [{"emailAddress": "sales@dxpe.com"}]}
             } if event_type == "Bounce" else None
    r = post(notif_api, ses_event_envelope(signing_key, event_type=event_type,
                                           message_id=f"ses-{event_type}",
                                           extra=extra))
    assert r.status_code == 200, r.text
    assert ns.get_notification(n["id"])["state"] == expected


def test_event_for_a_message_this_system_did_not_send_is_a_silent_noop(
        notif_api, signing_key):
    """A verified event on an allowlisted topic whose message id is unknown is
    a 200 no-op, NOT a 403: the endpoint must not become an oracle for which
    provider message ids this system has sent."""
    r = post(notif_api, ses_event_envelope(signing_key, event_type="Delivery",
                                           message_id="never-sent-by-us"))
    assert r.status_code == 200
    assert ns.list_notifications() == []


# ---------------------------------------------------------------------------
# Rejections — every one of them the same 403
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label", REJECTION_LABELS)
def test_every_rejection_is_403(notif_api, signing_key, label):
    sent_notification()
    r = post(notif_api, rejection_cases(signing_key)[label])
    assert r.status_code == 403, f"{label}: {r.status_code} {r.text}"


def test_all_rejections_are_byte_identical(notif_api, signing_key):
    """Reviewer R4 / guardrail 2: a prober must not be able to tell a bad
    signature from a foreign topic from a malformed body. Status, body bytes
    and the security headers all compared."""
    seen = set()
    for body in rejection_cases(signing_key).values():
        r = post(notif_api, body)
        seen.add((r.status_code, r.content,
                  r.headers.get("cache-control"), r.headers.get("referrer-policy")))
    assert len(seen) == 1, f"rejections are distinguishable: {seen}"
    status, content, _, _ = seen.pop()
    assert status == 403
    assert json.loads(content) == {"detail": "Forbidden"}


def test_an_empty_allowlist_rejects_a_perfectly_valid_envelope(
        notif_api, monkeypatch, signing_key):
    """Fail-closed config: no configured topic ⇒ nothing is accepted."""
    monkeypatch.setenv("SES_SNS_TOPIC_ARN_ALLOWLIST", "")
    r = post(notif_api, ses_event_envelope(signing_key, event_type="Delivery",
                                           message_id="ses-msg-1"))
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# D4 idempotency — keyed on (messageId, event type)
# ---------------------------------------------------------------------------

def test_a_replayed_event_is_a_noop(notif_api, signing_key):
    """SNS redelivers. The second delivery of the SAME event must change
    nothing — and must still answer 200, or SNS would keep retrying."""
    n = sent_notification(pmid="ses-replay")
    env = ses_event_envelope(signing_key, event_type="Open",
                             message_id="ses-replay")
    first = post(notif_api, env)
    opened_at = ns.get_notification(n["id"])["opened_at"]
    second = post(notif_api, env)
    assert (first.status_code, second.status_code) == (200, 200)
    after = ns.get_notification(n["id"])
    assert after["opened_at"] == opened_at
    applied = [e for e in ns.list_events(n["id"]) if e["event_type"] == "Open"]
    assert len(applied) == 1, "the replay wrote a second applied event row"


def test_idempotency_is_keyed_on_message_id_AND_event_type(notif_api, signing_key):
    """The key is the PAIR: the same message legitimately produces Delivery
    then Open then Click, and keying on the message id alone would silently
    drop everything after the first event."""
    n = sent_notification(pmid="ses-ladder")
    for event_type in ("Delivery", "Open", "Click"):
        r = post(notif_api, ses_event_envelope(signing_key, event_type=event_type,
                                               message_id="ses-ladder"))
        assert r.status_code == 200, r.text
    after = ns.get_notification(n["id"])
    assert after["state"] == ns.STATE_CLICKED
    assert after["delivered_at"] and after["opened_at"] and after["clicked_at"]


def test_an_out_of_order_event_never_walks_the_ladder_backwards(notif_api, signing_key):
    """A late Delivery arriving after a Click stamps its timestamp but does not
    demote the state (D3 monotonicity), and the audit shows the refusal."""
    n = sent_notification(pmid="ses-late")
    post(notif_api, ses_event_envelope(signing_key, event_type="Click",
                                       message_id="ses-late"))
    post(notif_api, ses_event_envelope(signing_key, event_type="Delivery",
                                       message_id="ses-late"))
    after = ns.get_notification(n["id"])
    assert after["state"] == ns.STATE_CLICKED
    assert after["delivered_at"], "the event's own timestamp is still recorded"
    refused = [e for e in ns.list_events(n["id"])
               if e["event_type"] == "Delivery" and not e["applied"]]
    assert refused, "the refused transition was not audited"


# ---------------------------------------------------------------------------
# SubscriptionConfirmation — and the ORDER that makes it safe
# ---------------------------------------------------------------------------

def test_confirmation_on_an_allowlisted_topic_visits_the_subscribe_url(
        notif_api, signing_key, no_subscribe_fetch):
    r = post(notif_api, subscription_confirmation(signing_key))
    assert r.status_code == 200, r.text
    assert no_subscribe_fetch == [
        "https://sns.us-east-1.amazonaws.com/?Action=ConfirmSubscription"]


def test_a_foreign_topics_subscribe_url_is_never_visited(
        notif_api, signing_key, no_subscribe_fetch):
    """D4's ordering invariant, as a fact about what this server fetched: the
    allowlist is checked BEFORE the SubscribeURL, so a stranger cannot use this
    endpoint as an HTTP client pointed at a URL of their choosing."""
    env = subscription_confirmation(
        signing_key, topic_arn=FOREIGN_TOPIC_ARN,
        subscribe_url="https://sns.us-east-1.amazonaws.com/?Action=Confirm&evil=1")
    r = post(notif_api, env)
    assert r.status_code == 403
    assert no_subscribe_fetch == []


def test_a_bad_signature_subscribe_url_is_never_visited(
        notif_api, signing_key, no_subscribe_fetch):
    env = {**subscription_confirmation(signing_key), "Signature": "AAAA"}
    assert post(notif_api, env).status_code == 403
    assert no_subscribe_fetch == []


def test_confirm_subscription_refuses_a_non_amazonaws_subscribe_url(monkeypatch):
    """The SubscribeURL is checked in its own right: an envelope can be
    genuinely signed by SNS on an allowlisted topic and still carry a
    SubscribeURL this server must not fetch."""
    import urllib.request
    fetched: list[str] = []
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda url, timeout=None: fetched.append(url))
    assert ses_webhook.confirm_subscription("https://attacker.test/confirm") is False
    assert fetched == []


# ---------------------------------------------------------------------------
# The pure helpers (no client, no store)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url,ok", [
    ("https://sns.us-east-1.amazonaws.com/cert.pem", True),
    ("https://amazonaws.com/cert.pem", True),
    ("https://SNS.US-EAST-1.AMAZONAWS.COM/cert.pem", True),
    ("https://sns.amazonaws.com.attacker.test/cert.pem", False),
    ("https://notamazonaws.com/cert.pem", False),
    ("http://sns.us-east-1.amazonaws.com/cert.pem", False),
    ("ftp://sns.us-east-1.amazonaws.com/cert.pem", False),
    ("", False),
    (None, False),
])
def test_certificate_url_ok(url, ok):
    assert ses_webhook.certificate_url_ok(url) is ok


def test_the_certificate_cache_is_bounded(monkeypatch):
    """The fetch sits after the topic allowlist but before the signature is
    known good, so an unauthenticated caller who guesses an allowlisted
    TopicArn decides which cert URLs get cached. The cache must not grow
    without limit, and the newest entry must survive the eviction."""
    import urllib.request
    monkeypatch.setattr(ses_webhook, "_CERT_CACHE", {})
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda url, timeout=None: _FakeResponse(url.encode()))

    urls = [f"https://sns.amazonaws.com/{i}.pem"
            for i in range(ses_webhook._CERT_CACHE_MAX * 3)]
    for url in urls:
        assert ses_webhook.fetch_certificate_pem(url) == url.encode()

    assert len(ses_webhook._CERT_CACHE) <= ses_webhook._CERT_CACHE_MAX
    assert urls[-1] in ses_webhook._CERT_CACHE
    assert urls[0] not in ses_webhook._CERT_CACHE


def test_a_cached_certificate_is_not_refetched(monkeypatch):
    """The reason the cache exists in the first place — a burst of events on one
    certificate is one fetch — still holds with the bound in place."""
    import urllib.request
    fetches: list[str] = []
    monkeypatch.setattr(ses_webhook, "_CERT_CACHE", {})

    def _record(url, timeout=None):
        fetches.append(url)
        return _FakeResponse(b"pem-bytes")

    monkeypatch.setattr(urllib.request, "urlopen", _record)
    url = "https://sns.amazonaws.com/one.pem"
    for _ in range(5):
        assert ses_webhook.fetch_certificate_pem(url) == b"pem-bytes"
    assert fetches == [url]


def test_canonical_string_omits_an_absent_subject_and_keeps_field_order():
    envelope = {"Type": "Notification", "MessageId": "m", "TopicArn": "t",
                "Message": "body", "Timestamp": "ts"}
    assert ses_webhook.canonical_string_to_sign(envelope) == (
        "Message\nbody\nMessageId\nm\nTimestamp\nts\nTopicArn\nt\n"
        "Type\nNotification\n")
    with_subject = ses_webhook.canonical_string_to_sign({**envelope, "Subject": "s"})
    assert "Subject\ns\n" in with_subject


def test_topic_allowed_is_exact_match_not_prefix(monkeypatch):
    monkeypatch.setenv("SES_SNS_TOPIC_ARN_ALLOWLIST", f"{TOPIC_ARN}, other-arn")
    assert ses_webhook.topic_allowed(TOPIC_ARN)
    assert ses_webhook.topic_allowed("other-arn")
    assert not ses_webhook.topic_allowed(TOPIC_ARN + "-staging")
    assert not ses_webhook.topic_allowed(None)


def test_parse_ses_event_reads_both_aws_shapes():
    """Configuration-set event publishing uses ``eventType``; the older
    per-identity feedback notifications use ``notificationType``. Both arrive
    on the same SNS topic in a real account."""
    modern = ses_webhook.parse_ses_event(json.dumps(
        {"eventType": "Bounce",
         "mail": {"messageId": "m1", "destination": ["a@b.com"]},
         "bounce": {"bounceType": "Permanent", "bounceSubType": "General",
                    "bouncedRecipients": [{"emailAddress": "a@b.com"}]}}))
    assert modern["event_type"] == "Bounce"
    assert modern["provider_message_id"] == "m1"
    assert modern["recipients"] == ["a@b.com"]
    assert modern["bounce_type"] == "Permanent"

    legacy = ses_webhook.parse_ses_event(
        {"notificationType": "Complaint",
         "mail": {"messageId": "m2"},
         "complaint": {"complainedRecipients": [{"emailAddress": "c@d.com"}],
                       "complaintFeedbackType": "abuse"}})
    assert legacy["event_type"] == "Complaint"
    assert legacy["recipients"] == ["c@d.com"]

    assert ses_webhook.parse_ses_event("{not json") is None
    assert ses_webhook.parse_ses_event({"mail": {"messageId": "m"}}) is None
    assert ses_webhook.parse_ses_event({"eventType": "Send", "mail": {}}) is None


def test_the_stored_bounce_detail_is_a_classification_not_the_payload():
    """``raw`` is persisted on the suppression row. A real bounce payload
    carries the recipient's headers and the remote MTA's diagnostic text;
    none of it needs storing, so none of it is."""
    parsed = ses_webhook.parse_ses_event(
        {"eventType": "Bounce",
         "mail": {"messageId": "m", "timestamp": "2026-09-20T10:00:00Z",
                  "headers": [{"name": "Subject", "value": "secret"}]},
         "bounce": {"bounceType": "Permanent", "bounceSubType": "General",
                    "bouncedRecipients": [
                        {"emailAddress": "a@b.com",
                         "diagnosticCode": "smtp; 550 user unknown"}]}})
    assert set(parsed["raw"]) == {"event_type", "bounce_type", "bounce_subtype",
                                  "complaint_feedback_type", "timestamp"}
    assert "secret" not in json.dumps(parsed["raw"])
    assert "550" not in json.dumps(parsed["raw"])


def test_handle_envelope_never_reaches_the_network(notif_api, signing_key,
                                                   monkeypatch):
    """D10 as an assertion rather than a promise: with the certificate seam
    replaced, the module's own fetch is never called — and if it were, this
    test would fail rather than open a socket."""
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: pytest.fail("a test opened a socket"))
    envelope = ses_event_envelope(signing_key, event_type="Delivery",
                                  message_id="ses-no-net")
    assert ses_webhook.handle_envelope(json.dumps(envelope)) is not None
