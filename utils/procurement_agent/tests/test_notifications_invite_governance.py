"""
Arc 4b T4 / R-F5 — invite mail is governed like auth mail, capped per account,
and names the inviter.

``MEMBER_INVITE`` is outward mail from our domain to a person who has never
heard of us. Arc 4 already put it in the governance ledger and on the
tracking-off ``gofer-auth`` configuration set (gate FINDING F7), and those two
properties are re-asserted HERE rather than assumed — a regression in either is
this arc's problem too. What arc 4b adds is the per-account daily cap and the
copy.

REVIEWER R6: every configuration-set assertion reads
``captured["ConfigurationSetName"]`` out of a botocore ``Stubber``-verified
``send_email`` call and compares it against the env value the TEST set. A
constant read from the code under test would prove nothing. No socket is
opened (D10).
"""
from __future__ import annotations

import email as email_lib
from datetime import datetime, timedelta, timezone

import pytest

from utils import (email_sender, mail_provider, notifications_store as ns,
                   supplier_accounts, supplier_registry)
from utils.procurement_agent.tests._arc4_notifications_fixtures import (  # noqa: F401
    AUTH_SET, NOTIFY_SET, isolate_notification_stores,
)

# The day the store will stamp on the rows it writes, derived from the same
# clock the store uses — so the count and the rows always agree, whatever
# calendar date the suite runs on. The cap function still takes the day as a
# PARAMETER (criterion 10); the tests below prove that by asking it about days
# other than this one.
DAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")
NEXT_DAY = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")


def capture_ses(monkeypatch, *, responses: int = 1):
    """Install ``SesProvider`` as THE transport and capture its call params."""
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
    monkeypatch.setattr(email_sender, "EMAIL_SEND_ENABLED", True)
    return captured, stubber


def delivered_text(captured_call: dict) -> str:
    """The DECODED text of a captured raw SES message — the MIME builder
    base64-encodes the body, so a substring check against the wire bytes
    would pass vacuously."""
    parsed = email_lib.message_from_bytes(captured_call["Content"]["Raw"]["Data"])
    parts = parsed.get_payload() if parsed.is_multipart() else [parsed]
    return "\n".join((p.get_payload(decode=True) or b"").decode("utf-8", "replace")
                     for p in parts)


@pytest.fixture
def invites(tmp_path, monkeypatch):
    """Arc-4 stores + flags on, and two accounts so cap isolation is testable."""
    isolate_notification_stores(tmp_path, monkeypatch)
    dxp = supplier_accounts.create_account("dxpe.com")
    other = supplier_accounts.create_account("motion.com")
    return dxp, other


def invite(email: str, *, domain: str = "dxpe.com",
           inviter: str = "dana@dxpe.com", day: str = DAY, member_id=None):
    return supplier_accounts.send_member_invite_email(
        email, account_domain=domain, invited_by_email=inviter,
        member_id=member_id, day=day)


# ---------------------------------------------------------------------------
# Governance: the configuration set and the ledger row (R-F5's first half)
# ---------------------------------------------------------------------------

def test_invite_goes_out_on_the_tracking_off_configuration_set(invites, monkeypatch):
    """Read from the Stubber-captured call, compared against the env the test
    set — reviewer R6."""
    captured, stubber = capture_ses(monkeypatch)
    assert invite("newbie@dxpe.com") == "sent"
    stubber.assert_no_pending_responses()
    assert captured[0]["ConfigurationSetName"] == AUTH_SET
    assert captured[0]["ConfigurationSetName"] != NOTIFY_SET


def test_invite_writes_a_ledger_row_in_the_auth_cap_class(invites, monkeypatch):
    capture_ses(monkeypatch)
    invite("newbie@dxpe.com")
    rows = supplier_registry.get_sent_messages(domain="dxpe.com")
    assert [r["message_class"] for r in rows] == [supplier_registry.MESSAGE_CLASS_AUTH]
    assert rows[0]["status"] == "sent"


# ---------------------------------------------------------------------------
# The copy names the inviter AND their company (R-F5's second half)
# ---------------------------------------------------------------------------

def test_the_body_names_the_inviting_member_and_their_company(invites, monkeypatch):
    """Without both, an invite from a domain the recipient has never seen is
    indistinguishable from phishing — and the safe reaction to phishing is to
    ignore it, which silently costs us the member."""
    captured, _ = capture_ses(monkeypatch)
    invite("newbie@dxpe.com", inviter="dana@dxpe.com")
    body = delivered_text(captured[0])
    assert "dana@dxpe.com" in body
    assert "dxpe.com" in body
    # ...and in the subject, which is what the recipient decides on first.
    parsed = email_lib.message_from_bytes(captured[0]["Content"]["Raw"]["Data"])
    assert "dana@dxpe.com" in str(parsed["Subject"])


def test_the_invite_still_carries_no_token(invites, monkeypatch):
    """The invite is an announcement, not a credential: it points at the
    sign-in screen so the person requests their own single-use link."""
    captured, _ = capture_ses(monkeypatch)
    invite("newbie@dxpe.com")
    body = delivered_text(captured[0])
    assert "?token=" not in body
    assert "/supplier/verify" in body


def test_an_unknown_inviter_degrades_rather_than_naming_nobody(invites, monkeypatch):
    captured, _ = capture_ses(monkeypatch)
    supplier_accounts.send_member_invite_email(
        "newbie@dxpe.com", account_domain="dxpe.com", day=DAY)
    body = delivered_text(captured[0])
    assert "A colleague at dxpe.com" in body


# ---------------------------------------------------------------------------
# The per-account daily cap
# ---------------------------------------------------------------------------

def test_the_eleventh_invite_in_a_day_is_refused(invites, monkeypatch):
    captured, _ = capture_ses(monkeypatch, responses=10)
    for i in range(10):
        assert invite(f"m{i}@dxpe.com") == "sent"
    assert invite("m10@dxpe.com") == "cap_blocked"
    assert len(captured) == 10, "nothing was sent past the cap"


def test_a_refused_invite_writes_no_ledger_row_and_no_mail(invites, monkeypatch):
    monkeypatch.setenv("INVITE_DAILY_CAP_PER_ACCOUNT", "1")
    captured, _ = capture_ses(monkeypatch, responses=1)
    assert invite("first@dxpe.com") == "sent"
    before = len(supplier_registry.get_sent_messages(domain="dxpe.com"))
    assert invite("second@dxpe.com") == "cap_blocked"
    assert len(supplier_registry.get_sent_messages(domain="dxpe.com")) == before
    assert len(captured) == 1, "the refused invite never reached the provider"


def test_one_accounts_cap_does_not_touch_another_account(invites, monkeypatch):
    """The blast radius is one member list. A busy account must not be able to
    shut the invite channel for everybody else."""
    captured, _ = capture_ses(monkeypatch, responses=11)
    for i in range(10):
        assert invite(f"m{i}@dxpe.com") == "sent"
    assert invite("m10@dxpe.com") == "cap_blocked"
    assert invite("hire@motion.com", domain="motion.com",
                  inviter="lee@motion.com") == "sent"


def test_the_cap_is_per_day_and_rearms(invites, monkeypatch):
    capture_ses(monkeypatch, responses=3)
    monkeypatch.setenv("INVITE_DAILY_CAP_PER_ACCOUNT", "1")
    assert invite("a@dxpe.com", day=DAY) == "sent"
    assert invite("b@dxpe.com", day=DAY) == "cap_blocked"
    assert invite("c@dxpe.com", day=NEXT_DAY) == "sent"


def test_the_cap_is_configurable_and_inert_at_zero(invites, monkeypatch):
    assert supplier_accounts.invite_daily_cap() == 10
    monkeypatch.setenv("INVITE_DAILY_CAP_PER_ACCOUNT", "3")
    assert supplier_accounts.invite_daily_cap() == 3
    monkeypatch.setenv("INVITE_DAILY_CAP_PER_ACCOUNT", "not-a-number")
    assert supplier_accounts.invite_daily_cap() == \
        supplier_accounts.DEFAULT_INVITE_DAILY_CAP
    monkeypatch.setenv("INVITE_DAILY_CAP_PER_ACCOUNT", "0")
    assert supplier_accounts.invite_cap_reached("dxpe.com", day=DAY) is False


def test_the_cap_decision_takes_the_day_as_a_parameter(invites, monkeypatch):
    """Criterion 10: no wall-clock read inside the decision. Same inputs, same
    answer, on any calendar date the suite happens to run."""
    capture_ses(monkeypatch, responses=1)
    monkeypatch.setenv("INVITE_DAILY_CAP_PER_ACCOUNT", "1")
    invite("a@dxpe.com", day=DAY)
    assert supplier_accounts.invite_cap_reached("dxpe.com", day=DAY) is True
    assert supplier_accounts.invite_cap_reached("dxpe.com", day=NEXT_DAY) is False
    assert supplier_accounts.invite_cap_reached("dxpe.com", day="1999-01-01") is False


# ---------------------------------------------------------------------------
# Flag OFF — today's behaviour exactly
# ---------------------------------------------------------------------------

def test_flag_off_sends_nothing_and_never_consults_the_cap(tmp_path, monkeypatch):
    isolate_notification_stores(tmp_path, monkeypatch, notifications_on=False)
    recorded = []

    class Recording(email_sender.GmailSender):
        def send(self, message):
            recorded.append(message)
            return super().send(message)

    monkeypatch.setattr(email_sender, "GmailSender", Recording)
    assert supplier_accounts.send_member_invite_email(
        "newbie@dxpe.com", account_domain="dxpe.com") is None
    assert recorded == []
    assert ns.list_notifications(kind=ns.KIND_MEMBER_INVITE) == []
