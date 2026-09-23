"""
utils/mail_provider.py
Arc 4 T2 — the ``MailProvider`` adapter (D1), with Amazon SES as the first
implementation.

WHERE THIS SITS, AND WHY IT MATTERS
-----------------------------------
This module is a TRANSPORT, not a sender. It is reached from exactly one
place — the provider step INSIDE ``GmailSender.send``
(``utils/email_sender.py``) — which is the one last seam every outbound in the
repo already passes through. The gate's G1 finding is the reason: that seam
runs the governance stack (suppression → allowlist → caps) and then the
``EMAIL_SEND_ENABLED`` delivery gate BEFORE the transport is chosen. Selecting
a provider below both of them means D1's "nothing bypasses send governance" is
a structural property of the call graph, not a convention every future call
site has to remember.

Adding a second ``EmailSender`` subclass instead — an ``SesSender`` a caller
picks — would put SES BESIDE governance rather than below it. That is the one
shape this module deliberately does not have.

CONTRACT (CLAUDE.md §9, external providers)
-------------------------------------------
- **Fail-soft, always.** No provider call raises into the send path. An error,
  a timeout, a missing key, a missing region → a ``ProviderSendResult`` with
  ``status="error"``, logged. The caller degrades; nothing crashes.
- **No-op cleanly without configuration.** No ``AWS_REGION`` and no
  credentials ⇒ ``SesProvider`` never constructs a client and returns an
  error result. boto3 itself is imported LAZILY so the dependency is not
  required to import this module or run the suite.
- **D2 is enforced here, fail-closed.** Auth mail (magic links, invites)
  carries ``metadata["auth_mail"]``; it is sent ONLY on the tracking-OFF
  configuration set, and if that set is unconfigured the send is REFUSED
  rather than silently falling back to the tracking-ON set. Click tracking
  rewrites links through SES's tracking domain, and corporate link scanners
  pre-fetch them — which would consume a single-use token before the human
  ever clicks it.
- **D10: tests never touch AWS.** ``FakeProvider`` covers the behavioural
  tests; ``SesProvider`` is exercised through a botocore ``Stubber``. There
  is no code path in the test suite that opens a socket to AWS.
"""
from __future__ import annotations

import base64
import os
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import make_msgid
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Config surface (G9 convention: named ENV_* constants + safe defaults, read
# LIVE at call time so a monkeypatch in a test always wins)
# ---------------------------------------------------------------------------

ENV_FLAG = "NOTIFICATIONS_V1"
ENV_AWS_REGION = "AWS_REGION"
ENV_CONFIG_SET_NOTIFICATIONS = "SES_CONFIGURATION_SET_NOTIFICATIONS"
ENV_CONFIG_SET_AUTH = "SES_CONFIGURATION_SET_AUTH"
ENV_FROM_ADDRESS = "SES_FROM_ADDRESS"
ENV_PROVIDER = "MAIL_PROVIDER"       # "ses" (default when the flag is on) | "fake"

PROVIDER_SES = "ses"
PROVIDER_FAKE = "fake"

# Test/dev override seam: a test installs a FakeProvider here rather than
# reaching into the selection logic. ``None`` ⇒ selection by config.
_OVERRIDE_PROVIDER: Optional["MailProvider"] = None


def _env_truthy(value: Optional[str]) -> bool:
    """Strict opt-in parse (house rule): only 1/true/yes/on enable."""
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def notifications_active() -> bool:
    """True iff ``NOTIFICATIONS_V1`` is truthy. Read at call time — the
    live-read style the gate's G9 recommends, so a test's ``setenv`` takes
    effect without a module reload and no ``conftest`` pin is required."""
    return _env_truthy(os.environ.get(ENV_FLAG))


def notifications_configuration_set() -> Optional[str]:
    """The D2 tracking-ON configuration set (open + click tracking).
    ``None`` when unconfigured — SES then applies the account default, which
    is the correct degradation for non-auth mail."""
    return (os.environ.get(ENV_CONFIG_SET_NOTIFICATIONS) or "").strip() or None


def auth_configuration_set() -> Optional[str]:
    """The D2 tracking-OFF configuration set. ``None`` when unconfigured —
    and auth mail is then REFUSED, never downgraded onto the tracking set."""
    return (os.environ.get(ENV_CONFIG_SET_AUTH) or "").strip() or None


def from_address() -> str:
    """The verified SES identity to send as. Falls back to the Gmail sender
    address so the From header is never empty in a half-configured env."""
    explicit = (os.environ.get(ENV_FROM_ADDRESS) or "").strip()
    if explicit:
        return explicit
    from utils import gmail_client
    return gmail_client.gmail_sender_address()


# ---------------------------------------------------------------------------
# The interface
# ---------------------------------------------------------------------------

@dataclass
class ProviderSendResult:
    """One provider attempt.

    ``status``: ``"sent"`` (the provider accepted and returned an id) or
    ``"error"`` (anything else — fail-soft, never an exception).
    ``provider_message_id`` is the id every later delivery event arrives on,
    so it is the join key D4's webhook resolves against.
    """
    status: str
    provider_message_id: Optional[str] = None
    configuration_set: Optional[str] = None
    error: Optional[str] = None
    provider: str = ""


class MailProvider(ABC):
    """Swappable outbound transport (D1). Implementations are fail-soft:
    ``send`` returns a ``ProviderSendResult`` and never raises."""

    name: str = "abstract"

    @abstractmethod
    def send(self, message: Any, *, sender: Optional[str] = None
             ) -> ProviderSendResult:
        ...


def message_configuration_set(message: Any) -> tuple[Optional[str], Optional[str]]:
    """Resolve the configuration set for one ``EmailMessage`` (D2).

    Returns ``(configuration_set, refusal_reason)``. A refusal reason is
    non-``None`` only for auth mail whose tracking-OFF set is unconfigured —
    the deliberate fail-closed case. An explicit ``metadata["configuration_set"]``
    wins for non-auth mail; otherwise the notifications set applies.
    """
    meta = getattr(message, "metadata", None) or {}
    if meta.get("auth_mail"):
        auth_set = auth_configuration_set()
        if not auth_set:
            return None, (
                f"auth mail requires the tracking-off configuration set "
                f"({ENV_CONFIG_SET_AUTH} is unset); refusing to send on a "
                f"tracking-enabled set")
        return auth_set, None
    explicit = (meta.get("configuration_set") or "").strip() or None
    return explicit or notifications_configuration_set(), None


#: The concierge alert kind raised when an auth-mail send is refused (R7, F-03).
ALERT_AUTH_MAIL_REFUSED = "AUTH_MAIL_REFUSED"


def raise_auth_refusal_alert(message: Any, refusal: str) -> None:
    """Raise one deduped ACTION_NOW alert for a refused auth-mail send.

    Fail-soft by construction: an alerting failure must never change what the
    caller returns, and must never surface to the supplier.
    """
    try:
        from utils import notifications_store
        meta = getattr(message, "metadata", None) or {}
        domain = meta.get("supplier_domain") or ""
        notifications_store.raise_alert(
            kind=ALERT_AUTH_MAIL_REFUSED,
            # Deduped on the CONFIGURATION fault, not the recipient: one
            # misconfiguration is one thing to fix, however many sends it blocks.
            dedupe_key=f"{ALERT_AUTH_MAIL_REFUSED}:{ENV_CONFIG_SET_AUTH}",
            tier=notifications_store.TIER_ACTION_NOW,
            supplier_domain=domain or None,
            detail={"reason": refusal, "missing_env": ENV_CONFIG_SET_AUTH,
                    "supplier_domain": domain},
        )
    except Exception as exc:
        print(f"[MailProvider] auth-refusal alert failed: {exc}")


def build_raw_message(message: Any, *, sender: str) -> bytes:
    """Build the RFC822 bytes SES sends as raw content.

    Raw (not SES's ``Simple`` content) because an RFQ can carry attachments
    and a Cc, and because one builder keeps the delivered bytes identical in
    shape to the Gmail path. Note SES assigns its OWN ``Message-ID`` to a raw
    message, so the id that matters downstream is the one ``SendEmail``
    returns — not a header set here.
    """
    body_part = MIMEText(getattr(message, "body", "") or "")
    attachments = getattr(message, "attachments", None) or []
    if attachments:
        mime: MIMEText | MIMEMultipart = MIMEMultipart("mixed")
        mime.attach(body_part)
        for att in attachments:
            maintype, _, subtype = (att.mime_type or "application/octet-stream").partition("/")
            part = MIMEBase(maintype, subtype or "octet-stream")
            part.set_payload(att.content)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=att.filename)
            mime.attach(part)
    else:
        mime = body_part
    mime["To"] = ", ".join(getattr(message, "to", []) or [])
    cc = getattr(message, "cc", None) or []
    if cc:
        mime["Cc"] = ", ".join(cc)
    mime["From"] = sender
    mime["Subject"] = getattr(message, "subject", "") or ""
    mime["Message-ID"] = make_msgid(domain="arkim.ai")
    return mime.as_bytes()


def message_tags(message: Any) -> list[dict]:
    """SES ``EmailTags`` for one message.

    Only the notification id and kind travel as tags: they come back on every
    SNS event, which is what lets a bounce be attributed without a database
    round-trip. Nothing identifying the recipient is tagged — tags land in
    CloudWatch and in the event stream, and SES restricts tag values to
    ``[A-Za-z0-9_-]`` anyway, so an address could not ride here even if it
    should (it should not).
    """
    meta = getattr(message, "metadata", None) or {}
    tags: list[dict] = []
    nid = meta.get("notification_id")
    if nid:
        tags.append({"Name": "notification_id", "Value": _tag_safe(str(nid))})
    kind = meta.get("notification_kind")
    if kind:
        tags.append({"Name": "notification_kind", "Value": _tag_safe(str(kind))})
    return tags


def _tag_safe(value: str) -> str:
    """SES tag values accept only ``[A-Za-z0-9_-]``, max 256 chars. Anything
    else is replaced rather than rejected — a malformed tag must not be able
    to fail an otherwise-valid send."""
    cleaned = "".join(ch if (ch.isalnum() or ch in "_-") else "-" for ch in value)
    return cleaned[:256] or "-"


# ---------------------------------------------------------------------------
# FakeProvider — tests and dev (D10)
# ---------------------------------------------------------------------------

class FakeProvider(MailProvider):
    """In-memory transport. Records what would have gone out and returns a
    synthetic provider message id, so the whole notification lifecycle —
    including the webhook, which needs a provider id to join on — is
    exercisable with no AWS and no network."""

    name = PROVIDER_FAKE

    def __init__(self) -> None:
        self.outbox: list[dict] = []
        self.fail_next: bool = False

    def send(self, message: Any, *, sender: Optional[str] = None
             ) -> ProviderSendResult:
        config_set, refusal = message_configuration_set(message)
        # R7 (arc 5, F-03): the tracking-off configuration set is an SES concept.
        # Under the fake provider — dev, demo and the evaluation harness — there is
        # no tracking domain to rewrite a magic link through, so requiring the set
        # here only blocks auth mail in environments that cannot leak it. Auth mail
        # is captured normally. `message_configuration_set` itself stays
        # provider-agnostic, so every existing assertion on it is unchanged.
        if refusal:
            refusal = None
            config_set = None
        if self.fail_next:
            self.fail_next = False
            return ProviderSendResult(status="error", error="fake provider failure",
                                      provider=self.name)
        mid = f"fake-{uuid.uuid4()}"
        self.outbox.append({
            "to": list(getattr(message, "to", []) or []),
            "cc": list(getattr(message, "cc", []) or []),
            "subject": getattr(message, "subject", ""),
            "body": getattr(message, "body", ""),
            "metadata": dict(getattr(message, "metadata", None) or {}),
            "configuration_set": config_set,
            "tags": message_tags(message),
            "provider_message_id": mid,
            "sender": sender or from_address(),
        })
        return ProviderSendResult(status="sent", provider_message_id=mid,
                                  configuration_set=config_set, provider=self.name)


# ---------------------------------------------------------------------------
# SesProvider — the real thing (boto3 SESv2 SendEmail)
# ---------------------------------------------------------------------------

class SesProvider(MailProvider):
    """Amazon SES transport via boto3 ``sesv2:SendEmail``.

    ``client`` is injectable so a test can drive a botocore ``Stubber``
    against a real client object with no network (D10). With no injected
    client and no ``AWS_REGION``, no client is ever constructed and ``send``
    returns a fail-soft error — the CLAUDE.md §9 "no-op cleanly without a
    key" contract.
    """

    name = PROVIDER_SES

    def __init__(self, client: Optional[Any] = None,
                 region: Optional[str] = None) -> None:
        self._client = client
        self._region = region

    def _resolve_client(self) -> Optional[Any]:
        """Lazily build the SESv2 client. boto3 is imported HERE, not at
        module scope, so importing this module (and running the suite) never
        requires the dependency to be installed or configured."""
        if self._client is not None:
            return self._client
        region = self._region or (os.environ.get(ENV_AWS_REGION) or "").strip()
        if not region:
            print("[MailProvider/ses] no AWS_REGION configured — no-op")
            return None
        try:
            import boto3  # noqa: PLC0415 — deliberate lazy import
            self._client = boto3.client("sesv2", region_name=region)
            return self._client
        except Exception as exc:
            print(f"[MailProvider/ses] client construction failed: "
                  f"{type(exc).__name__}: {exc}")
            return None

    def send(self, message: Any, *, sender: Optional[str] = None
             ) -> ProviderSendResult:
        config_set, refusal = message_configuration_set(message)
        if refusal:
            # D2, fail-closed: no tracking-off set ⇒ auth mail does not go.
            print(f"[MailProvider/ses] REFUSED: {refusal}")
            # R7 (arc 5, F-03): fail LOUDLY — to the operator. A refused auth send
            # is a misconfiguration nobody would otherwise see: the supplier just
            # never receives a sign-in link. Deduped at the store (unique index),
            # so a burst of refusals is one alert.
            raise_auth_refusal_alert(message, refusal)
            return ProviderSendResult(status="error", error=refusal,
                                      provider=self.name)
        client = self._resolve_client()
        if client is None:
            return ProviderSendResult(status="error",
                                      error="SES not configured (no region/client)",
                                      provider=self.name)
        addr = sender or from_address()
        params: dict[str, Any] = {
            "FromEmailAddress": addr,
            "Destination": {
                "ToAddresses": list(getattr(message, "to", []) or []),
                "CcAddresses": list(getattr(message, "cc", []) or []),
            },
            "Content": {"Raw": {"Data": build_raw_message(message, sender=addr)}},
        }
        if config_set:
            params["ConfigurationSetName"] = config_set
        tags = message_tags(message)
        if tags:
            params["EmailTags"] = tags
        try:
            resp = client.send_email(**params)
        except Exception as exc:
            # Fail-soft by contract: a throttle, a credential failure or a
            # network error degrades the send, it never raises into the
            # sourcing/notification pipeline.
            print(f"[MailProvider/ses] send failed: {type(exc).__name__}: {exc}")
            return ProviderSendResult(status="error", error=str(exc),
                                      provider=self.name)
        mid = (resp or {}).get("MessageId")
        if not mid:
            return ProviderSendResult(status="error",
                                      error="SES returned no MessageId",
                                      provider=self.name)
        print(f"[MailProvider/ses] SENT (ses_id={mid} config_set={config_set})")
        return ProviderSendResult(status="sent", provider_message_id=mid,
                                  configuration_set=config_set, provider=self.name)


# ---------------------------------------------------------------------------
# Selection (D1 / T2) — flag OFF ⇒ None ⇒ the Gmail path, untouched
# ---------------------------------------------------------------------------

def active_provider_name() -> Optional[str]:
    """Which provider ``active_provider`` would return, without building one.

    ``None`` when notifications are off (the Gmail path). Used by the R7 boot
    guard, which must not construct an SES client just to ask a config question.
    """
    if _OVERRIDE_PROVIDER is not None:
        return getattr(_OVERRIDE_PROVIDER, "name", None)
    if not notifications_active():
        return None
    return (os.environ.get(ENV_PROVIDER) or PROVIDER_SES).strip().lower()


def active_provider() -> Optional[MailProvider]:
    """The transport for this send, or ``None`` to mean "use Gmail".

    ``None`` whenever ``NOTIFICATIONS_V1`` is off — which is what makes the
    flag-off path byte-identical to today's behaviour: ``email_sender`` sees
    no provider and runs its existing Gmail code.
    """
    if _OVERRIDE_PROVIDER is not None:
        return _OVERRIDE_PROVIDER
    if not notifications_active():
        return None
    choice = (os.environ.get(ENV_PROVIDER) or PROVIDER_SES).strip().lower()
    if choice == PROVIDER_FAKE:
        return FakeProvider()
    return SesProvider()
