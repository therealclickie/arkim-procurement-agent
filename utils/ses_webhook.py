"""
utils/ses_webhook.py
Arc 4 T6 / D4 — the SNS envelope layer in front of SES delivery events.

WHAT THIS MODULE IS DEFENDING
------------------------------
``POST /api/webhooks/ses`` is a **public, unauthenticated** endpoint: anyone
on the internet can post JSON at it. Everything that makes it safe lives here,
and the order is the security property:

    1. envelope type recognised          (Notification / SubscriptionConfirmation)
    2. TopicArn is in the allowlist      <- BEFORE anything is fetched or visited
    3. SigningCertURL is an amazonaws.com https URL
    4. the SNS signature verifies against that certificate
    5. only then is the payload believed, and only then is a SubscribeURL visited

Step 2 sits ahead of steps 3-5 deliberately. A confirmation envelope carries a
``SubscribeURL`` that the receiver is expected to GET; visiting one before the
topic is known would let a stranger's topic use this server as an HTTP client
against a URL of their choosing. Allowlist first means the only subscription
this app can ever confirm is one on a topic its own operator configured.

Every rejection returns the SAME thing to the caller: the API layer maps a
``None`` from :func:`handle_envelope` to a uniform 403 with no detail, so a
prober cannot tell an unknown topic from a bad signature from a malformed
body. Nothing from the request body is logged.

FAIL-CLOSED CONFIG
------------------
An empty / unset ``SES_SNS_TOPIC_ARN_ALLOWLIST`` allows NO topic — a
misconfigured deployment rejects every envelope rather than accepting every
envelope. The same direction as the send-governance allowlist.

NO NETWORK IN TESTS (D10): the certificate fetch is one function
(:func:`fetch_certificate_pem`) that tests replace; the signature verification
itself runs for real against a real certificate.
"""
from __future__ import annotations

import base64
import json
import os
from typing import Any, Optional
from urllib.parse import urlparse

ENV_TOPIC_ALLOWLIST = "SES_SNS_TOPIC_ARN_ALLOWLIST"

TYPE_NOTIFICATION = "Notification"
TYPE_SUBSCRIPTION_CONFIRMATION = "SubscriptionConfirmation"
TYPE_UNSUBSCRIBE_CONFIRMATION = "UnsubscribeConfirmation"

# The fields SNS signs, per envelope type, in the canonical (alphabetical)
# order AWS documents. Subject is included only when present.
_SIGNED_FIELDS: dict[str, tuple[str, ...]] = {
    TYPE_NOTIFICATION: ("Message", "MessageId", "Subject", "Timestamp",
                        "TopicArn", "Type"),
    TYPE_SUBSCRIPTION_CONFIRMATION: ("Message", "MessageId", "SubscribeURL",
                                     "Timestamp", "Token", "TopicArn", "Type"),
    TYPE_UNSUBSCRIBE_CONFIRMATION: ("Message", "MessageId", "SubscribeURL",
                                    "Timestamp", "Token", "TopicArn", "Type"),
}

# Certificates are immutable at their URL and rotate rarely; caching keeps a
# burst of events from becoming a burst of certificate fetches.
_CERT_CACHE: dict[str, bytes] = {}

_HTTP_TIMEOUT = 5


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def allowed_topic_arns() -> frozenset[str]:
    """The configured TopicArn allowlist (comma-separated env).

    Unset ⇒ EMPTY ⇒ every envelope is rejected. Fail-closed: a deployment
    that forgot to configure the topic must not accept arbitrary events.
    """
    raw = os.environ.get(ENV_TOPIC_ALLOWLIST) or ""
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def topic_allowed(topic_arn: Optional[str]) -> bool:
    """True iff this exact TopicArn is allowlisted (exact match, no prefix
    logic — an ARN is a full identifier and a prefix match would accept a
    different account's topic that happens to share a stem)."""
    return bool(topic_arn) and topic_arn in allowed_topic_arns()


# ---------------------------------------------------------------------------
# Signature verification (SNS SignatureVersion 1 and 2)
# ---------------------------------------------------------------------------

def canonical_string_to_sign(envelope: dict) -> str:
    """The exact byte string SNS signed, rebuilt from the envelope.

    Any field SNS signs that we fail to include, or include in the wrong
    order, makes every signature fail — so this is deliberately the documented
    field list and nothing clever.
    """
    fields = _SIGNED_FIELDS.get(str(envelope.get("Type") or ""), ())
    parts: list[str] = []
    for name in fields:
        value = envelope.get(name)
        if value is None:
            continue            # Subject is genuinely optional; the rest are not
        parts.append(f"{name}\n{value}\n")
    return "".join(parts)


def certificate_url_ok(url: Optional[str]) -> bool:
    """True iff ``url`` is an https URL on an ``amazonaws.com`` host (D4).

    The host test is on the parsed hostname and anchored on a dot, so
    ``https://sns.us-east-1.amazonaws.com.attacker.test/c.pem`` and
    ``https://notamazonaws.com/c.pem`` both fail. Without this, an attacker
    signs an envelope with their own key and points this server at their own
    certificate — the signature would verify perfectly and mean nothing.
    """
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    return host == "amazonaws.com" or host.endswith(".amazonaws.com")


def fetch_certificate_pem(url: str) -> Optional[bytes]:
    """Fetch (and cache) the SNS signing certificate.

    THE seam tests replace, so no test opens a socket (D10). Callers must
    have checked :func:`certificate_url_ok` first — this function does not
    re-authorise the URL, it only fetches it.
    """
    cached = _CERT_CACHE.get(url)
    if cached is not None:
        return cached
    try:
        from urllib.request import urlopen
        with urlopen(url, timeout=_HTTP_TIMEOUT) as resp:   # noqa: S310 — host checked
            pem = resp.read()
    except Exception as exc:
        print(f"[SesWebhook] certificate fetch failed: {type(exc).__name__}")
        return None
    _CERT_CACHE[url] = pem
    return pem


def verify_signature(envelope: dict) -> bool:
    """Verify the SNS signature over the canonical string. Fail-soft False.

    ``SignatureVersion`` 1 is SHA1 (what SNS still emits by default) and 2 is
    SHA256; anything else is refused rather than guessed.
    """
    cert_url = envelope.get("SigningCertURL") or envelope.get("SigningCertUrl")
    if not certificate_url_ok(cert_url):
        return False
    signature = envelope.get("Signature")
    if not signature:
        return False
    version = str(envelope.get("SignatureVersion") or "")
    pem = fetch_certificate_pem(str(cert_url))
    if not pem:
        return False
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        if version == "1":
            algorithm: Any = hashes.SHA1()
        elif version == "2":
            algorithm = hashes.SHA256()
        else:
            return False
        cert = x509.load_pem_x509_certificate(pem)
        cert.public_key().verify(
            base64.b64decode(signature),
            canonical_string_to_sign(envelope).encode("utf-8"),
            padding.PKCS1v15(), algorithm)
        return True
    except Exception:
        # Invalid signature, malformed certificate, malformed base64: all the
        # same answer, and none of them is logged with any request content.
        return False


# ---------------------------------------------------------------------------
# SES event normalisation
# ---------------------------------------------------------------------------

def parse_ses_event(message: Any) -> Optional[dict]:
    """Normalise one SES event (the JSON inside the SNS ``Message``).

    Handles both shapes AWS emits: configuration-set event publishing
    (``eventType``) and the older per-identity feedback notifications
    (``notificationType``). Returns
    ``{"event_type", "provider_message_id", "recipients", "bounce_type",
    "bounce_subtype", "raw"}`` or ``None`` when it is not an SES event.

    ``raw`` is a SUMMARY, not the payload: it is persisted on the suppression
    row, and a bounce payload carries the recipient's headers and diagnostic
    text. Nothing needs storing beyond the classification.
    """
    if isinstance(message, (str, bytes)):
        try:
            message = json.loads(message)
        except (ValueError, TypeError):
            return None
    if not isinstance(message, dict):
        return None
    event_type = message.get("eventType") or message.get("notificationType")
    if not event_type:
        return None
    mail = message.get("mail") or {}
    provider_message_id = mail.get("messageId")
    if not provider_message_id:
        return None

    bounce = message.get("bounce") or {}
    complaint = message.get("complaint") or {}
    delivery = message.get("delivery") or {}
    if bounce:
        recipients = [r.get("emailAddress") for r
                      in (bounce.get("bouncedRecipients") or [])]
    elif complaint:
        recipients = [r.get("emailAddress") for r
                      in (complaint.get("complainedRecipients") or [])]
    elif delivery.get("recipients"):
        recipients = list(delivery.get("recipients") or [])
    else:
        recipients = list(mail.get("destination") or [])

    return {
        "event_type": str(event_type),
        "provider_message_id": str(provider_message_id),
        "recipients": [r for r in recipients if r],
        "bounce_type": bounce.get("bounceType"),
        "bounce_subtype": bounce.get("bounceSubType"),
        "raw": {"event_type": str(event_type),
                "bounce_type": bounce.get("bounceType"),
                "bounce_subtype": bounce.get("bounceSubType"),
                "complaint_feedback_type": complaint.get("complaintFeedbackType"),
                "timestamp": mail.get("timestamp")},
    }


def confirm_subscription(subscribe_url: str) -> bool:
    """GET the ``SubscribeURL`` to complete an SNS subscription.

    Only ever reached for an ALLOWLISTED, signature-verified envelope, and the
    URL itself must still be an amazonaws.com https URL — an envelope that
    passes every other check but points its SubscribeURL at a third party is
    still refused, because the check protects a different thing (what this
    server will fetch) than the signature does (who wrote the envelope).
    """
    if not certificate_url_ok(subscribe_url):
        print("[SesWebhook] refusing a non-amazonaws SubscribeURL")
        return False
    try:
        from urllib.request import urlopen
        with urlopen(subscribe_url, timeout=_HTTP_TIMEOUT):  # noqa: S310 — host checked
            pass
        print("[SesWebhook] subscription confirmed")
        return True
    except Exception as exc:
        print(f"[SesWebhook] subscription confirmation failed: {type(exc).__name__}")
        return False


# ---------------------------------------------------------------------------
# The one entry point the API layer calls
# ---------------------------------------------------------------------------

def handle_envelope(body: Any) -> Optional[dict]:
    """Verify and apply one SNS envelope.

    Returns a small outcome dict on success, or ``None`` for EVERY rejection —
    the caller turns that into one uniform 403 so the endpoint reveals nothing
    about which check failed.

    Outcomes:
      ``{"type": "Notification", "applied": bool}`` — applied is False for a
      replayed event (D4 idempotency) or an event for a message this system
      did not send; both are successes, not errors.
      ``{"type": "SubscriptionConfirmation", "confirmed": bool}``.
    """
    envelope = body
    if isinstance(envelope, (str, bytes)):
        try:
            envelope = json.loads(envelope)
        except (ValueError, TypeError):
            return None
    if not isinstance(envelope, dict):
        return None

    env_type = str(envelope.get("Type") or "")
    if env_type not in _SIGNED_FIELDS:
        return None
    # (2) allowlist FIRST — before any URL in this envelope is fetched.
    if not topic_allowed(envelope.get("TopicArn")):
        return None
    # (3)+(4) certificate host, then the signature over the canonical string.
    if not verify_signature(envelope):
        return None

    if env_type in (TYPE_SUBSCRIPTION_CONFIRMATION, TYPE_UNSUBSCRIBE_CONFIRMATION):
        confirmed = confirm_subscription(str(envelope.get("SubscribeURL") or ""))
        return {"type": env_type, "confirmed": confirmed}

    event = parse_ses_event(envelope.get("Message"))
    if event is None:
        # Signed by SNS on an allowlisted topic, but not an SES event we
        # model: accept it and do nothing rather than 403 a legitimate
        # publisher (and rather than let a prober distinguish the two).
        return {"type": env_type, "applied": False}
    from utils import notifications
    applied = notifications.apply_delivery_event(event)
    return {"type": env_type, "applied": applied,
            "event_type": event["event_type"]}
