"""
Arc 4 T9 / D8 — bounces and complaints suppress the ADDRESS, and alert.

THE ONE STRUCTURAL DECISION THIS FILE PINS
------------------------------------------
Suppression is **address-level, in arc 4's own store** — never
``send_governance.suppression_add``, which is DOMAIN-level and permanent until
an admin lifts it (gate FINDING F3). One member's full mailbox must not silence
every colleague at that supplier and every RFQ to them. A test here asserts the
governance suppression list is untouched, because "we suppressed the right
thing" is invisible otherwise.

WHAT THE BRIEF ASKS FOR
-----------------------
"hard bounce suppresses and alerts; complaint suppresses and alerts; soft bounce
×2 no action, ×3 alerts; suppressed member excluded from fan-out."
"""
from __future__ import annotations

import pytest

from utils import notifications, notifications_store as ns, send_governance
from utils.procurement_agent.tests._arc4_notifications_fixtures import (  # noqa: F401
    allowlist, install_fake_provider, isolate_notification_stores, open_rfq,
)

ADDR = "sales@dxpe.com"


@pytest.fixture
def bounces(tmp_path, monkeypatch):
    """Arc-4 stores + flags, the real governance gate, a fake transport."""
    isolate_notification_stores(tmp_path, monkeypatch)
    monkeypatch.setenv("SEND_GOVERNANCE_V1", "1")
    provider = install_fake_provider(monkeypatch)
    allowlist("dxpe.com")
    return provider


def sent(*, pmid: str, recipient: str = ADDR, kind: str = ns.KIND_RFQ_NEW,
         member_id: str = "m-1") -> dict:
    n = ns.create_notification(kind=kind, account_id="acct-1",
                               member_id=member_id, run_id="run-1",
                               supplier_domain="dxpe.com", recipient=recipient,
                               is_test=True)
    assert n is not None
    ns.set_provider_message_id(n["id"], pmid)
    ns.transition(n["id"], ns.STATE_SENT, event_type="Send")
    return ns.get_notification(n["id"])


def event(*, event_type: str, pmid: str, recipients=(ADDR,),
          bounce_type: str | None = None, bounce_subtype: str | None = None) -> dict:
    """A normalised SES event, as ``ses_webhook.parse_ses_event`` produces one."""
    return {"event_type": event_type, "provider_message_id": pmid,
            "recipients": list(recipients), "bounce_type": bounce_type,
            "bounce_subtype": bounce_subtype,
            "raw": {"event_type": event_type, "bounce_type": bounce_type}}


# ---------------------------------------------------------------------------
# Hard bounce and complaint: suppress + alert
# ---------------------------------------------------------------------------

def test_a_hard_bounce_suppresses_the_address_and_alerts(bounces):
    n = sent(pmid="p-hard")
    assert notifications.apply_delivery_event(
        event(event_type="Bounce", pmid="p-hard", bounce_type="Permanent",
              bounce_subtype="General")) is True

    assert ns.get_notification(n["id"])["state"] == ns.STATE_BOUNCED
    assert ns.is_email_suppressed(ADDR) is True
    (alert,) = ns.list_alerts(kind=ns.ALERT_EMAIL_SUPPRESSED)
    assert alert["email"] == ADDR
    assert alert["detail"]["reason"] == "hard_bounce"
    assert alert["status"] == ns.ALERT_OPEN


def test_a_complaint_suppresses_the_address_and_alerts(bounces):
    """A complaint is someone pressing "this is spam". Continuing to mail them
    damages the sending domain's reputation for every other supplier."""
    n = sent(pmid="p-complaint")
    assert notifications.apply_delivery_event(
        event(event_type="Complaint", pmid="p-complaint")) is True
    assert ns.get_notification(n["id"])["state"] == ns.STATE_COMPLAINED
    assert ns.is_email_suppressed(ADDR) is True
    (alert,) = ns.list_alerts(kind=ns.ALERT_EMAIL_SUPPRESSED)
    assert alert["detail"]["reason"] == "complaint"


def test_suppression_is_address_level_and_never_touches_the_domain(bounces):
    """Gate FINDING F3, as an assertion. ``suppression_add`` would silence the
    whole supplier — every colleague and every RFQ — until an admin lifted it."""
    sent(pmid="p-f3")
    notifications.apply_delivery_event(
        event(event_type="Bounce", pmid="p-f3", bounce_type="Permanent"))
    assert ns.is_email_suppressed(ADDR) is True
    assert send_governance.suppression_list() == []
    assert ns.is_email_suppressed("buyer@dxpe.com") is False


def test_suppression_normalises_the_address(bounces):
    sent(pmid="p-case", recipient="Sales@DXPE.com")
    notifications.apply_delivery_event(
        event(event_type="Bounce", pmid="p-case", recipients=("Sales@DXPE.com",),
              bounce_type="Permanent"))
    assert ns.is_email_suppressed("sales@dxpe.com") is True
    assert ns.is_email_suppressed("SALES@DXPE.COM") is True


def test_a_bounce_on_a_sign_in_link_suppresses_and_alerts_too(bounces):
    """Why auth mail is tracked at all (T5): without this, a member whose
    address hard-bounces simply never receives a sign-in link again and nobody
    finds out."""
    n = sent(pmid="p-auth", kind=ns.KIND_AUTH_MAGIC_LINK)
    notifications.apply_delivery_event(
        event(event_type="Bounce", pmid="p-auth", bounce_type="Permanent"))
    assert ns.get_notification(n["id"])["state"] == ns.STATE_BOUNCED
    assert ns.is_email_suppressed(ADDR) is True
    assert ns.list_alerts(kind=ns.ALERT_EMAIL_SUPPRESSED)


def test_a_bounce_for_an_unknown_message_still_suppresses_its_recipient(bounces):
    """The event carries the bounced address itself, so a bounce for a message
    this system cannot resolve (a pre-arc-4 send, a cleared row) still protects
    the address. Losing that would let a known-bad address keep being mailed."""
    assert notifications.apply_delivery_event(
        event(event_type="Bounce", pmid="p-unknown", bounce_type="Permanent")) is True
    assert ns.is_email_suppressed(ADDR) is True


def test_a_replayed_bounce_does_not_raise_a_second_alert(bounces):
    sent(pmid="p-replay")
    ev = event(event_type="Bounce", pmid="p-replay", bounce_type="Permanent")
    assert notifications.apply_delivery_event(ev) is True
    assert notifications.apply_delivery_event(ev) is False
    assert len(ns.list_alerts(kind=ns.ALERT_EMAIL_SUPPRESSED)) == 1


# ---------------------------------------------------------------------------
# Soft bounces: counted, never suppressed
# ---------------------------------------------------------------------------

def soft(pmid: str) -> bool:
    return notifications.apply_delivery_event(
        event(event_type="Bounce", pmid=pmid, bounce_type="Transient",
              bounce_subtype="MailboxFull"))


def test_two_soft_bounces_do_nothing_and_the_third_alerts(bounces):
    """D8's exact shape: a full mailbox works again tomorrow, so a soft bounce
    must not suppress; a run of three is a real problem for a human to look at."""
    for i in (1, 2):
        sent(pmid=f"p-soft-{i}")
        assert soft(f"p-soft-{i}") is True
        assert ns.is_email_suppressed(ADDR) is False
        assert ns.list_alerts(kind=ns.ALERT_SOFT_BOUNCE_REPEATED) == []
    assert ns.soft_bounce_count(ADDR) == 2

    sent(pmid="p-soft-3")
    assert soft("p-soft-3") is True
    assert ns.is_email_suppressed(ADDR) is False, "a soft bounce must not suppress"
    (alert,) = ns.list_alerts(kind=ns.ALERT_SOFT_BOUNCE_REPEATED)
    assert alert["email"] == ADDR
    assert alert["detail"]["consecutive"] == 3


def test_a_long_run_of_soft_bounces_raises_ONE_alert(bounces):
    """Alert fatigue is a real failure mode: an operator who gets forty-eight
    rows for one broken mailbox stops reading the queue."""
    for i in range(1, 9):
        sent(pmid=f"p-many-{i}")
        soft(f"p-many-{i}")
    assert ns.soft_bounce_count(ADDR) == 8
    assert len(ns.list_alerts(kind=ns.ALERT_SOFT_BOUNCE_REPEATED)) == 1


def test_a_delivery_resets_the_streak_and_a_new_streak_alerts_again(bounces):
    """"Consecutive" has to mean consecutive, and the second streak is its own
    problem — so it gets its own alert rather than being deduped away."""
    for i in (1, 2, 3):
        sent(pmid=f"p-s1-{i}")
        soft(f"p-s1-{i}")
    assert len(ns.list_alerts(kind=ns.ALERT_SOFT_BOUNCE_REPEATED)) == 1

    sent(pmid="p-ok")
    notifications.apply_delivery_event(event(event_type="Delivery", pmid="p-ok"))
    assert ns.soft_bounce_count(ADDR) == 0

    for i in (1, 2):
        sent(pmid=f"p-s2-{i}")
        soft(f"p-s2-{i}")
    assert len(ns.list_alerts(kind=ns.ALERT_SOFT_BOUNCE_REPEATED)) == 1
    sent(pmid="p-s2-3")
    soft("p-s2-3")
    assert len(ns.list_alerts(kind=ns.ALERT_SOFT_BOUNCE_REPEATED)) == 2


def test_the_streak_is_per_address(bounces):
    for i in (1, 2, 3):
        sent(pmid=f"p-a-{i}", recipient="a@dxpe.com")
        notifications.apply_delivery_event(
            event(event_type="Bounce", pmid=f"p-a-{i}", recipients=("a@dxpe.com",),
                  bounce_type="Transient"))
        sent(pmid=f"p-b-{i}", recipient="b@dxpe.com")
        notifications.apply_delivery_event(
            event(event_type="Bounce", pmid=f"p-b-{i}", recipients=("b@dxpe.com",),
                  bounce_type="Transient"))
    assert ns.soft_bounce_count("a@dxpe.com") == 3
    assert ns.soft_bounce_count("b@dxpe.com") == 3
    assert len(ns.list_alerts(kind=ns.ALERT_SOFT_BOUNCE_REPEATED)) == 2


def test_a_bounce_with_no_subtype_and_no_type_counts_as_soft(bounces):
    """SES always sends a bounceType, but an unrecognised value must degrade to
    the SAFE side: suppressing an address on a malformed event would silently
    cut off a working supplier."""
    sent(pmid="p-vague")
    notifications.apply_delivery_event(
        event(event_type="Bounce", pmid="p-vague", bounce_type=None))
    assert ns.is_email_suppressed(ADDR) is False
    assert ns.soft_bounce_count(ADDR) == 1


# ---------------------------------------------------------------------------
# The consequence: a suppressed address stops being notified
# ---------------------------------------------------------------------------

def test_a_suppressed_member_is_excluded_from_the_fanout(bounces):
    from utils import supplier_accounts
    acct = supplier_accounts.create_account("dxpe.com")
    good = supplier_accounts.add_member(acct["id"], "good@dxpe.com",
                                        role=supplier_accounts.ROLE_OWNER,
                                        status=supplier_accounts.MEMBER_ACTIVE)
    bad = supplier_accounts.add_member(acct["id"], "bad@dxpe.com",
                                       role=supplier_accounts.ROLE_MEMBER,
                                       status=supplier_accounts.MEMBER_ACTIVE)
    assert {m["id"] for m in notifications.notifiable_members(acct["id"])} == \
        {good["id"], bad["id"]}

    ns.suppress_email("bad@dxpe.com", reason="hard_bounce", is_test=True)
    assert [m["id"] for m in notifications.notifiable_members(acct["id"])] == \
        [good["id"]]

    sm_id = open_rfq(domain="dxpe.com", run_id="run-fan")
    created = notifications.notify_rfq_new(
        {"run_id": "run-fan", "supplier_domain": "dxpe.com",
         "sent_message_id": sm_id}, acct)
    assert [n["recipient"] for n in created] == ["good@dxpe.com"]
    assert [m["to"] for m in bounces.outbox] == [["good@dxpe.com"]]


def test_an_account_whose_only_member_is_suppressed_becomes_an_alert(bounces):
    """The promise is "you will never miss an RFQ". If there is nobody left to
    tell, that is a human problem, not a silent no-op."""
    from utils import supplier_accounts
    acct = supplier_accounts.create_account("dxpe.com")
    supplier_accounts.add_member(acct["id"], ADDR,
                                 role=supplier_accounts.ROLE_OWNER,
                                 status=supplier_accounts.MEMBER_ACTIVE)
    ns.suppress_email(ADDR, reason="complaint", is_test=True)
    sm_id = open_rfq(domain="dxpe.com", run_id="run-none")
    assert notifications.notify_rfq_new(
        {"run_id": "run-none", "supplier_domain": "dxpe.com",
         "sent_message_id": sm_id}, acct) == []
    (alert,) = ns.list_alerts(kind=ns.ALERT_NO_NOTIFIABLE_MEMBERS)
    assert alert["run_id"] == "run-none"
    assert bounces.outbox == []


# ---------------------------------------------------------------------------
# D8's "the NDR text parsing is bypassed for SES-originated events"
# ---------------------------------------------------------------------------

def test_the_ses_path_never_parses_bounce_text(bounces, monkeypatch):
    """SES hands over a structured bounceType/bounceSubType, so the heuristic
    NDR text parser (``bounce_parser.parse_bounce``) is not in this path at all.
    Asserted by making it fail loudly if it is ever reached."""
    from utils import bounce_parser
    monkeypatch.setattr(bounce_parser, "parse_bounce",
                        lambda raw: pytest.fail("SES events must not be text-parsed"))
    sent(pmid="p-structured")
    notifications.apply_delivery_event(
        event(event_type="Bounce", pmid="p-structured", bounce_type="Permanent",
              bounce_subtype="Suppressed"))
    assert ns.is_email_suppressed(ADDR) is True


def test_the_flag_off_path_applies_nothing(bounces, monkeypatch):
    sent(pmid="p-off")
    monkeypatch.setenv("NOTIFICATIONS_V1", "")
    assert notifications.apply_delivery_event(
        event(event_type="Bounce", pmid="p-off", bounce_type="Permanent")) is False
    assert ns.is_email_suppressed(ADDR) is False
    assert ns.list_alerts(status=None) == []
