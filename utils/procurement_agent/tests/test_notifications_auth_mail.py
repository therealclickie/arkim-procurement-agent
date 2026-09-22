"""
Arc 4 T5 — auth mail on the tracking-OFF configuration set (D2).

WHY THIS FILE IS WRITTEN AGAINST THE STUBBER-CAPTURED CALL
-----------------------------------------------------------
Reviewer R6: the configuration set must be asserted from the parameters SES
was actually called with, not from a constant the code under test also reads.
So every assertion here reads ``captured["ConfigurationSetName"]`` out of a
botocore ``Stubber``-verified ``send_email`` call and compares it against the
env value the test itself set — if the code read the wrong env var, or fell
back to the notifications set, the comparison fails.

The failure this prevents is concrete: ``gofer-notifications`` has click
tracking on, which rewrites every link through SES's tracking domain, where a
corporate link scanner (Microsoft Safe Links and friends) pre-fetches it. A
single-use sign-in token behind that is a token consumed before the human
clicks. D2 is therefore fail-CLOSED — no tracking-off set configured means the
auth mail does not go at all.

NO NETWORK (D10): the SES client is a real client object with a ``Stubber``
attached, primed with inline credentials so botocore never consults the
ambient environment, ``~/.aws`` or an instance-metadata endpoint.
"""
from __future__ import annotations

import email as email_lib

import pytest

from utils import (email_sender, mail_provider, notifications_store as ns,
                   supplier_accounts, supplier_registry)
from utils.procurement_agent.tests._arc4_notifications_fixtures import (  # noqa: F401
    APP_ORIGIN, AUTH_SET, NOTIFY_SET, admin_headers, isolate_notification_stores,
    login, notif_api,
)


class CapturingSes:
    """A stand-in SES client that records calls WITHOUT answering them.

    Used only for the fail-closed assertions, where the point is that
    ``send_email`` is never reached at all: a ``Stubber`` would also fail an
    unexpected call, but it could not distinguish "refused before the client"
    from "called and errored".
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send_email(self, **kw):  # pragma: no cover - asserted never called
        self.calls.append(kw)
        return {"MessageId": "should-not-happen"}


def delivered_text(captured_call: dict) -> str:
    """The decoded text of a captured raw SES message. The body is
    base64-transfer-encoded by the MIME builder, so a substring check against
    the wire bytes would pass vacuously — this decodes it first."""
    parsed = email_lib.message_from_bytes(captured_call["Content"]["Raw"]["Data"])
    parts = parsed.get_payload() if parsed.is_multipart() else [parsed]
    return "\n".join(
        (p.get_payload(decode=True) or b"").decode("utf-8", "replace")
        for p in parts)


def capture_ses(monkeypatch, *, responses: int = 1):
    """Install ``SesProvider`` as THE transport and capture its call params.

    Returns ``(captured, stubber)``: ``captured`` is the list of
    ``send_email`` kwargs, one per send. The stubber is returned so the test
    can ``assert_no_pending_responses()``.
    """
    import boto3
    from botocore.stub import Stubber
    client = boto3.client("sesv2", region_name="us-east-1",
                          aws_access_key_id="test", aws_secret_access_key="test")
    stubber = Stubber(client)
    for i in range(responses):
        stubber.add_response("send_email", {"MessageId": f"ses-{i}"},
                             expected_params=None)
    stubber.activate()
    captured: list[dict] = []
    real_send = client.send_email

    def capturing(**kw):
        captured.append(kw)
        return real_send(**kw)

    client.send_email = capturing
    monkeypatch.setattr(mail_provider, "_OVERRIDE_PROVIDER",
                        mail_provider.SesProvider(client=client))
    # The delivery gate sits ABOVE the provider and conftest pins it off.
    monkeypatch.setattr(email_sender, "EMAIL_SEND_ENABLED", True)
    return captured, stubber


@pytest.fixture
def auth_env(tmp_path, monkeypatch):
    """Arc-4 stores + flags on, an account with one ACTIVE member."""
    isolate_notification_stores(tmp_path, monkeypatch)
    account = supplier_accounts.create_account("dxpe.com")
    member = supplier_accounts.add_member(account["id"], "sales@dxpe.com",
                                          role=supplier_accounts.ROLE_OWNER,
                                          status=supplier_accounts.MEMBER_ACTIVE)
    return account, member


# ---------------------------------------------------------------------------
# D2 — the magic link goes out on the tracking-OFF set, and never the other one
# ---------------------------------------------------------------------------

def test_magic_link_uses_the_auth_configuration_set(auth_env, monkeypatch):
    captured, stubber = capture_ses(monkeypatch)
    status = supplier_accounts.send_magic_link_email(
        "sales@dxpe.com", "raw-token-1", account_domain="dxpe.com")
    stubber.assert_no_pending_responses()
    assert status == "sent"
    assert len(captured) == 1
    # Asserted from the CAPTURED call against the env the test set (R6).
    assert captured[0]["ConfigurationSetName"] == AUTH_SET


def test_magic_link_never_uses_the_notifications_set(auth_env, monkeypatch):
    captured, _ = capture_ses(monkeypatch)
    supplier_accounts.send_magic_link_email(
        "sales@dxpe.com", "raw-token-2", account_domain="dxpe.com")
    assert captured[0]["ConfigurationSetName"] != NOTIFY_SET


def test_auth_send_carries_no_tracking_tags(auth_env, monkeypatch):
    """Tags ride into the SES event stream and CloudWatch. An auth message
    carries none: there is no notification id to correlate at send time, and
    nothing about a sign-in attempt belongs in a metrics dimension."""
    captured, _ = capture_ses(monkeypatch)
    supplier_accounts.send_magic_link_email(
        "sales@dxpe.com", "raw-token-3", account_domain="dxpe.com")
    assert "EmailTags" not in captured[0]


def test_auth_mail_is_refused_when_the_tracking_off_set_is_unconfigured(
        auth_env, monkeypatch):
    """Fail-CLOSED (D2): no ``gofer-auth`` ⇒ the send does not happen at all,
    rather than silently falling back to the tracking-enabled set."""
    monkeypatch.setenv("SES_CONFIGURATION_SET_AUTH", "")
    client = CapturingSes()
    monkeypatch.setattr(mail_provider, "_OVERRIDE_PROVIDER",
                        mail_provider.SesProvider(client=client))
    monkeypatch.setattr(email_sender, "EMAIL_SEND_ENABLED", True)
    status = supplier_accounts.send_magic_link_email(
        "sales@dxpe.com", "raw-token-4", account_domain="dxpe.com")
    assert status == "error"
    assert client.calls == [], "auth mail reached SES with no tracking-off set"


def test_the_raw_token_is_in_the_delivered_body_and_nowhere_else(
        auth_env, monkeypatch):
    """The credential lives in the message and in no store — the ledger row
    deliberately carries no body (D9's write is subject/recipient/class)."""
    captured, _ = capture_ses(monkeypatch)
    supplier_accounts.send_magic_link_email(
        "sales@dxpe.com", "raw-token-5", account_domain="dxpe.com")
    assert "raw-token-5" in delivered_text(captured[0])
    rows = supplier_registry.get_sent_messages(domain="dxpe.com")
    assert rows and all("raw-token-5" not in (r.get("body") or "") for r in rows)


# ---------------------------------------------------------------------------
# D9 — the auth send is ledgered AND tracked
# ---------------------------------------------------------------------------

def test_magic_link_writes_an_auth_class_ledger_row(auth_env, monkeypatch):
    capture_ses(monkeypatch)
    supplier_accounts.send_magic_link_email(
        "sales@dxpe.com", "raw-token-6", account_domain="dxpe.com")
    rows = supplier_registry.get_sent_messages(domain="dxpe.com")
    assert len(rows) == 1
    assert rows[0]["message_class"] == supplier_registry.MESSAGE_CLASS_AUTH
    assert rows[0]["status"] == "sent"


def test_magic_link_is_tracked_as_a_notification_bound_to_the_provider_id(
        auth_env, monkeypatch):
    captured, _ = capture_ses(monkeypatch)
    supplier_accounts.send_magic_link_email(
        "sales@dxpe.com", "raw-token-7", account_domain="dxpe.com")
    tracked = ns.list_notifications(kind=ns.KIND_AUTH_MAGIC_LINK)
    assert len(tracked) == 1
    assert tracked[0]["state"] == ns.STATE_SENT
    assert tracked[0]["configuration_set"] == AUTH_SET
    # The join key every later SES event arrives on (D4).
    assert tracked[0]["provider_message_id"] == "ses-0"
    assert tracked[0]["recipient"] == "sales@dxpe.com"


def test_auth_kinds_never_enter_the_escalation_ladder(auth_env, monkeypatch):
    """An unseen sign-in link is not an unanswered RFQ. Neither auth kind is
    escalatable, so no amount of time turns one into a concierge alert."""
    assert ns.KIND_AUTH_MAGIC_LINK not in ns.ESCALATABLE_KINDS
    assert ns.KIND_MEMBER_INVITE not in ns.ESCALATABLE_KINDS


# ---------------------------------------------------------------------------
# Flag off — today's behaviour exactly (prime directive 2)
# ---------------------------------------------------------------------------

def test_flag_off_magic_link_writes_no_ledger_row_and_no_notification(
        tmp_path, monkeypatch):
    isolate_notification_stores(tmp_path, monkeypatch, notifications_on=False)
    recorded = []

    class Recording(email_sender.GmailSender):
        def send(self, message):
            recorded.append(message)
            return super().send(message)

    monkeypatch.setattr(email_sender, "GmailSender", Recording)
    status = supplier_accounts.send_magic_link_email(
        "sales@dxpe.com", "raw-token-8", account_domain="dxpe.com")
    assert status == "stubbed"                      # the Gmail path, gate shut
    assert recorded and "auth_mail" not in recorded[0].metadata
    assert supplier_registry.get_sent_messages(domain="dxpe.com") == []
    assert ns.list_notifications() == []


def test_flag_off_invite_sends_nothing_at_all(tmp_path, monkeypatch):
    """Before arc 4 an invite sent no mail (gate FINDING F5). Flag off, that
    is still exactly true — the new channel is entirely behind the flag."""
    isolate_notification_stores(tmp_path, monkeypatch, notifications_on=False)
    recorded = []

    class Recording(email_sender.GmailSender):
        def send(self, message):
            recorded.append(message)
            return super().send(message)

    monkeypatch.setattr(email_sender, "GmailSender", Recording)
    out = supplier_accounts.send_member_invite_email(
        "newbie@dxpe.com", account_domain="dxpe.com")
    assert out is None and recorded == []


# ---------------------------------------------------------------------------
# The invite channel (T5's second half)
# ---------------------------------------------------------------------------

def test_invite_mail_goes_on_the_auth_set_and_is_tracked(auth_env, monkeypatch):
    captured, _ = capture_ses(monkeypatch)
    account, _member = auth_env
    invited = supplier_accounts.add_member(account["id"], "newbie@dxpe.com",
                                           role=supplier_accounts.ROLE_MEMBER,
                                           status=supplier_accounts.MEMBER_ACTIVE)
    status = supplier_accounts.send_member_invite_email(
        "newbie@dxpe.com", account_domain="dxpe.com",
        invited_by_email="sales@dxpe.com", member_id=invited["id"])
    assert status == "sent"
    assert captured[0]["ConfigurationSetName"] == AUTH_SET
    assert captured[0]["Destination"]["ToAddresses"] == ["newbie@dxpe.com"]
    tracked = ns.list_notifications(kind=ns.KIND_MEMBER_INVITE)
    assert len(tracked) == 1 and tracked[0]["state"] == ns.STATE_SENT
    assert tracked[0]["member_id"] == invited["id"]


def test_invite_mail_carries_no_token(auth_env, monkeypatch):
    """The invite is an announcement, not a credential: it points at the
    sign-in screen and lets the person request their own single-use link."""
    captured, _ = capture_ses(monkeypatch)
    supplier_accounts.send_member_invite_email(
        "newbie@dxpe.com", account_domain="dxpe.com")
    body = delivered_text(captured[0])
    assert "?token=" not in body
    assert "/supplier/verify" in body


def test_invite_is_ledgered_in_the_auth_cap_class(auth_env, monkeypatch):
    capture_ses(monkeypatch)
    supplier_accounts.send_member_invite_email(
        "newbie@dxpe.com", account_domain="dxpe.com")
    rows = supplier_registry.get_sent_messages(domain="dxpe.com")
    assert [r["message_class"] for r in rows] == [supplier_registry.MESSAGE_CLASS_AUTH]


def test_auth_mail_does_not_consume_the_rfq_daily_cap(auth_env, monkeypatch):
    """Gate FINDING F2, stated as a test: ten sign-in links must not exhaust
    the RFQ budget. The class discriminator is what makes D9's ledger write
    safe to turn on."""
    capture_ses(monkeypatch, responses=3)
    for i in range(3):
        supplier_accounts.send_magic_link_email(
            "sales@dxpe.com", f"raw-token-cap-{i}", account_domain="dxpe.com")
    from datetime import datetime, timezone
    day = datetime.now(timezone.utc).date().isoformat()
    assert supplier_registry.count_send_attempts_utc_day(
        day, message_class=supplier_registry.MESSAGE_CLASS_RFQ) == 0
    assert supplier_registry.count_send_attempts_utc_day(
        day, message_class=supplier_registry.MESSAGE_CLASS_AUTH) == 3


# ---------------------------------------------------------------------------
# The invite ROUTE fires the channel (the api_server seam T5 adds)
# ---------------------------------------------------------------------------

def test_invite_route_sends_the_invite_mail(notif_api, monkeypatch):
    """End to end through the real session door: MANAGE_MEMBERS invite ⇒ the
    invited colleague is actually told, on the auth set."""
    sa = notif_api._sa
    account = sa.create_account("dxpe.com")
    sa.add_member(account["id"], "sales@dxpe.com", role=sa.ROLE_OWNER,
                  status=sa.MEMBER_ACTIVE)
    login(notif_api, monkeypatch)                     # magic-link send #1
    captured, _ = capture_ses(monkeypatch, responses=1)
    r = notif_api.post("/api/supplier/members/invite",
                       json={"email": "newbie@dxpe.com", "role": "MEMBER"},
                       headers={"Origin": APP_ORIGIN})
    assert r.status_code == 200, r.text
    assert len(captured) == 1
    assert captured[0]["Destination"]["ToAddresses"] == ["newbie@dxpe.com"]
    assert captured[0]["ConfigurationSetName"] == AUTH_SET
    assert [n["kind"] for n in ns.list_notifications(kind=ns.KIND_MEMBER_INVITE)]         == [ns.KIND_MEMBER_INVITE]
