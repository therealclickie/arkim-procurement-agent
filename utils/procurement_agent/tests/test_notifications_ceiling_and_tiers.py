"""
Arc 4b T10 / S5+S6 — the per-member daily ceiling, and tiered concierge alerts.

TWO RULES, ONE PRINCIPLE
------------------------
S5: no mailbox receives more than ``MEMBER_DAILY_NOTIFICATION_CEILING``
notification emails in a business day. **The ceiling DEFERS; it never
discards.** A dropped notification would be the single failure this whole
surface exists to prevent, so every test below that exercises the ceiling also
asserts where the deferred item went.

S6: only the top tier interrupts. A queue in which every row is equally urgent
has no urgency in it, and the first thing a human does with such a queue is
stop reading it — which costs us the alerts that were real.

Auth mail and invites are exempt from the ceiling by construction: they never
pass through ``_send_notification_mail``. That is asserted rather than
asserted-in-prose.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from utils import (business_hours, notifications, notifications_store as ns,
                   supplier_accounts)
from utils.procurement_agent.tests._arc4_notifications_fixtures import (  # noqa: F401
    allowlist, install_fake_provider, isolate_notification_stores,
)

NOW = datetime(2026, 9, 22, 15, 0, tzinfo=timezone.utc)      # Tue 08:00 PT
NEXT_DAY = NOW + timedelta(days=1)


@pytest.fixture
def surface(tmp_path, monkeypatch):
    isolate_notification_stores(tmp_path, monkeypatch)
    monkeypatch.setenv("SEND_GOVERNANCE_V1", "1")
    provider = install_fake_provider(monkeypatch)
    allowlist("dxpe.com")
    return provider


def account_with_owner(domain: str = "dxpe.com"):
    acct = supplier_accounts.create_account(domain)
    owner = supplier_accounts.add_member(
        acct["id"], f"owner@{domain}", role=supplier_accounts.ROLE_OWNER,
        status=supplier_accounts.MEMBER_ACTIVE)
    return acct, owner


def notify(acct, run_id: str, *, at: datetime):
    return notifications._notify_rfq_new(
        {"run_id": run_id, "supplier_domain": acct["supplier_domain"],
         "sent_message_id": f"sm-{run_id}", "manufacturer": "Goulds",
         "part_number": run_id}, acct, now=at)


def send_n_anchors(acct, count: int, *, start: datetime = NOW):
    """``count`` separate RFQ emails, each in its own coalescing window."""
    for i in range(count):
        notify(acct, f"run-{i}", at=start + timedelta(minutes=30 * i))


# ---------------------------------------------------------------------------
# S5 — the ceiling defers, it never discards
# ---------------------------------------------------------------------------

def test_the_sixth_notification_in_a_day_is_deferred_not_sent(surface):
    acct, _ = account_with_owner()
    send_n_anchors(acct, 5)
    assert len(surface.outbox) == 5

    created = notify(acct, "run-6", at=NOW + timedelta(hours=3))
    assert len(surface.outbox) == 5, "the sixth email was not sent"
    sixth = ns.get_notification(created[0]["id"])
    assert sixth["state"] == ns.STATE_QUEUED
    assert sixth["deferred"] == 1, "it was DEFERRED, not dropped"


def test_the_deferred_sixth_is_delivered_by_the_digest(surface):
    """The other half of "defers, never discards": prove it arrives."""
    acct, _ = account_with_owner()
    send_n_anchors(acct, 5)
    created = notify(acct, "run-6", at=NOW + timedelta(hours=3))
    surface.outbox.clear()

    out = notifications.run_daily_digest()
    assert out == {"members": 1, "notifications": 1}
    assert len(surface.outbox) == 1
    assert ns.get_notification(created[0]["id"])["state"] == ns.STATE_SENT
    assert "run-6" in surface.outbox[0]["body"]


def test_a_whole_coalesced_batch_over_the_ceiling_is_deferred_together(surface):
    acct, _ = account_with_owner()
    send_n_anchors(acct, 5)
    later = NOW + timedelta(hours=3)
    notify(acct, "run-a", at=later)                       # deferred by ceiling
    notify(acct, "run-b", at=later + timedelta(minutes=1))
    assert len(surface.outbox) == 5

    assert notifications.run_coalesced_sends(now=later + timedelta(minutes=30)) == \
        {"batches": 0, "notifications": 0}
    pending = ns.list_notifications(kind=ns.KIND_RFQ_NEW, deferred=True)
    assert {p["run_id"] for p in pending} == {"run-a", "run-b"}
    assert notifications.run_daily_digest()["notifications"] == 2


def test_the_ceiling_rearms_the_next_business_day(surface):
    acct, _ = account_with_owner()
    send_n_anchors(acct, 5)
    notify(acct, "run-tomorrow", at=NEXT_DAY)
    assert len(surface.outbox) == 6


def test_a_reminder_over_the_ceiling_is_held_and_nothing_is_claimed(
        surface, monkeypatch):
    """The reminder path defers too — and because no parent is claimed, every
    one of those requests is carried into the next day's reminder.

    The escalation rung is pushed out of the way: the question here is what
    the CEILING does to a reminder, and a request that crosses the
    one-business-day escalation threshold overnight would answer a different
    one.
    """
    monkeypatch.setenv("ESCALATE_ALERT_HOURS", "40")
    acct, _ = account_with_owner()
    send_n_anchors(acct, 5)
    parent = ns.create_notification(
        kind=ns.KIND_RFQ_NEW, account_id=acct["id"], member_id="m-1",
        run_id="run-old", supplier_domain="dxpe.com",
        recipient="owner@dxpe.com", is_test=True)
    ns.transition(parent["id"], ns.STATE_SENT, event_type="Send",
                  at=business_hours.business_hours_before(NOW, 6).isoformat())

    assert notifications.run_escalations(NOW)["reminded"] == 0
    assert ns.get_notification(parent["id"])["reminded_at"] is None
    assert notifications.run_escalations(NEXT_DAY)["reminded"] == 1


def test_auth_mail_and_invites_are_exempt_from_the_ceiling(surface):
    """S5's exemption, asserted rather than promised: they are requested by
    the recipient or their colleague, not pushed by us — and they never pass
    through the notification send path where the ceiling lives."""
    acct, owner = account_with_owner()
    send_n_anchors(acct, 5)
    surface.outbox.clear()

    assert supplier_accounts.send_magic_link_email(
        "owner@dxpe.com", "tok", account_domain="dxpe.com",
        member_id=owner["id"]) == "sent"
    assert supplier_accounts.send_member_invite_email(
        "newbie@dxpe.com", account_domain="dxpe.com",
        invited_by_email="owner@dxpe.com") == "sent"
    assert len(surface.outbox) == 2


def test_the_ceiling_is_configurable_and_inert_at_zero(surface, monkeypatch):
    assert notifications.member_daily_ceiling() == 5
    monkeypatch.setenv("MEMBER_DAILY_NOTIFICATION_CEILING", "2")
    assert notifications.member_daily_ceiling() == 2
    monkeypatch.setenv("MEMBER_DAILY_NOTIFICATION_CEILING", "nonsense")
    assert notifications.member_daily_ceiling() == \
        notifications.DEFAULT_MEMBER_DAILY_CEILING
    monkeypatch.setenv("MEMBER_DAILY_NOTIFICATION_CEILING", "0")
    acct, _ = account_with_owner()
    send_n_anchors(acct, 8)
    assert len(surface.outbox) == 8


def test_the_ceiling_takes_the_day_as_a_parameter(surface):
    """Criterion 10: no wall-clock read inside the calculation."""
    acct, _ = account_with_owner()
    send_n_anchors(acct, 5)
    assert notifications.ceiling_reached("owner@dxpe.com", "2026-09-22") is True
    assert notifications.ceiling_reached("owner@dxpe.com", "2026-09-23") is False
    assert notifications.ceiling_reached("someone@else.com", "2026-09-22") is False


def test_the_ceiling_counts_emails_not_requests(surface):
    """Five coalesced requests in ONE email cost ONE slot, not five — the
    promise is about what lands in the mailbox."""
    acct, _ = account_with_owner()
    for i in range(5):
        notify(acct, f"run-{i}", at=NOW + timedelta(minutes=i))
    notifications.run_coalesced_sends(now=NOW + timedelta(minutes=30))
    assert len(surface.outbox) == 2                       # anchor + one batch
    assert notifications.ceiling_reached("owner@dxpe.com", "2026-09-22") is False


# ---------------------------------------------------------------------------
# S6 — tiers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind,tier", [
    (ns.ALERT_NO_NOTIFIABLE_MEMBERS, ns.TIER_ACTION_NOW),
    (ns.ALERT_EMAIL_SUPPRESSED, ns.TIER_ACTION_NOW),
    (ns.ALERT_RFQ_ESCALATION, ns.TIER_QUEUE),
    (ns.ALERT_SOFT_BOUNCE_REPEATED, ns.TIER_DIGEST),
    (ns.ALERT_NOTIFICATION_CAP_BLOCKED, ns.TIER_DIGEST),
])
def test_each_alert_kind_lands_in_its_specified_tier(surface, kind, tier):
    raised = ns.raise_alert(kind=kind, dedupe_key=f"k-{kind}", is_test=True)
    assert raised["tier"] == tier


def test_an_explicit_tier_overrides_the_kinds_default():
    assert ns.alert_tier(ns.ALERT_EMAIL_SUPPRESSED) == ns.TIER_ACTION_NOW
    assert ns.alert_tier(ns.ALERT_EMAIL_SUPPRESSED, ns.TIER_DIGEST) == ns.TIER_DIGEST
    assert ns.alert_tier(ns.ALERT_EMAIL_SUPPRESSED, "NONSENSE") == ns.TIER_ACTION_NOW
    assert ns.alert_tier("SOMETHING_NEW") == ns.TIER_QUEUE


def test_the_queue_never_lists_a_digest_tier_alert(surface):
    ns.raise_alert(kind=ns.ALERT_RFQ_ESCALATION, dedupe_key="e1", is_test=True)
    ns.raise_alert(kind=ns.ALERT_SOFT_BOUNCE_REPEATED, dedupe_key="b1", is_test=True)
    ns.raise_alert(kind=ns.ALERT_NOTIFICATION_CAP_BLOCKED, dedupe_key="c1",
                   is_test=True)
    queue = notifications.list_open_alerts()
    assert [a["kind"] for a in queue] == [ns.ALERT_RFQ_ESCALATION]
    assert all(a["tier"] in ns.QUEUE_TIERS for a in queue)


def test_the_digest_tier_is_delivered_once_daily_and_not_lost(surface):
    ns.raise_alert(kind=ns.ALERT_SOFT_BOUNCE_REPEATED, dedupe_key="b1", is_test=True)
    ns.raise_alert(kind=ns.ALERT_NOTIFICATION_CAP_BLOCKED, dedupe_key="c1",
                   is_test=True)
    digest = notifications.run_concierge_digest()
    assert digest["count"] == 2
    assert {a["kind"] for a in digest["alerts"]} == {
        ns.ALERT_SOFT_BOUNCE_REPEATED, ns.ALERT_NOTIFICATION_CAP_BLOCKED}
    # The digest REPORTS; it does not acknowledge. "Somebody was told" and
    # "somebody dealt with it" are different facts.
    assert all(a["status"] == ns.ALERT_OPEN for a in digest["alerts"])


def test_a_hard_bounce_on_the_last_contact_is_action_now(surface):
    """Losing one of three contacts is housekeeping. Losing the last one means
    the next RFQ to that supplier reaches nobody at all."""
    acct, owner = account_with_owner()
    n = ns.create_notification(kind=ns.KIND_RFQ_NEW, account_id=acct["id"],
                               member_id=owner["id"], supplier_domain="dxpe.com",
                               recipient="owner@dxpe.com", is_test=True)
    ns.set_provider_message_id(n["id"], "ses-1")
    notifications.apply_delivery_event(
        {"event_type": "Bounce", "provider_message_id": "ses-1",
         "recipients": ["owner@dxpe.com"], "bounce_type": "Permanent",
         "bounce_subtype": "General", "raw": {}})
    (alert,) = ns.list_alerts(kind=ns.ALERT_EMAIL_SUPPRESSED)
    assert alert["tier"] == ns.TIER_ACTION_NOW
    assert alert["detail"]["sole_contact"] is True


def test_a_hard_bounce_on_a_non_sole_contact_is_digest(surface):
    acct, owner = account_with_owner()
    supplier_accounts.add_member(acct["id"], "admin@dxpe.com",
                                 role=supplier_accounts.ROLE_ADMIN,
                                 status=supplier_accounts.MEMBER_ACTIVE)
    n = ns.create_notification(kind=ns.KIND_RFQ_NEW, account_id=acct["id"],
                               member_id=owner["id"], supplier_domain="dxpe.com",
                               recipient="owner@dxpe.com", is_test=True)
    ns.set_provider_message_id(n["id"], "ses-2")
    notifications.apply_delivery_event(
        {"event_type": "Bounce", "provider_message_id": "ses-2",
         "recipients": ["owner@dxpe.com"], "bounce_type": "Permanent",
         "bounce_subtype": "General", "raw": {}})
    (alert,) = ns.list_alerts(kind=ns.ALERT_EMAIL_SUPPRESSED)
    assert alert["tier"] == ns.TIER_DIGEST
    assert alert["detail"]["sole_contact"] is False
    assert notifications.list_open_alerts() == []


# ---------------------------------------------------------------------------
# S6 — escalations aggregate per supplier account per business day
# ---------------------------------------------------------------------------

def unseen(acct, run_id: str, *, hours: float = 30.0):
    n = ns.create_notification(kind=ns.KIND_RFQ_NEW, account_id=acct["id"],
                               member_id="m-1", run_id=run_id,
                               supplier_domain="dxpe.com",
                               recipient="owner@dxpe.com", is_test=True)
    ns.transition(n["id"], ns.STATE_SENT, event_type="Send",
                  at=business_hours.business_hours_before(NOW, hours).isoformat())
    return ns.get_notification(n["id"])


def test_three_unresponsive_rfqs_from_one_account_produce_one_escalation(surface):
    """Three requests, one phone call. Three queue rows would be two pieces of
    work a human opens before discovering they are the same conversation."""
    acct, _ = account_with_owner()
    parents = [unseen(acct, f"run-{i}") for i in range(3)]
    notifications.run_escalations(NOW)

    alerts = ns.list_alerts(kind=ns.ALERT_RFQ_ESCALATION)
    assert len(alerts) == 1
    assert alerts[0]["account_id"] == acct["id"]
    assert alerts[0]["detail"]["business_day"] == "2026-09-22"
    # The per-REQUEST guarantee is unchanged: each one is marked escalated, so
    # none of them can ladder again.
    assert all(ns.get_notification(p["id"])["escalated_at"] for p in parents)


def test_two_accounts_escalate_independently(surface):
    a, _ = account_with_owner("dxpe.com")
    b, _ = account_with_owner("motion.com")
    unseen(a, "run-a")
    nb = ns.create_notification(kind=ns.KIND_RFQ_NEW, account_id=b["id"],
                                run_id="run-b", supplier_domain="motion.com",
                                recipient="owner@motion.com", is_test=True)
    ns.transition(nb["id"], ns.STATE_SENT, event_type="Send",
                  at=business_hours.business_hours_before(NOW, 30).isoformat())
    notifications.run_escalations(NOW)
    assert len(ns.list_alerts(kind=ns.ALERT_RFQ_ESCALATION)) == 2


def test_the_next_business_day_can_escalate_again(surface):
    """Aggregation is per DAY: a supplier still silent tomorrow is a new
    conversation, not a duplicate of yesterday's."""
    acct, _ = account_with_owner()
    unseen(acct, "run-a")
    notifications.run_escalations(NOW)
    unseen(acct, "run-b", hours=30)
    notifications.run_escalations(NEXT_DAY)
    alerts = ns.list_alerts(kind=ns.ALERT_RFQ_ESCALATION)
    assert len(alerts) == 2
    assert {a["detail"]["business_day"] for a in alerts} == {"2026-09-22",
                                                             "2026-09-23"}


def test_alert_once_survives_the_coarser_key(surface):
    """Arc 4's alert-once guarantee, in its new form: running twice with the
    same ``now`` changes nothing."""
    acct, _ = account_with_owner()
    unseen(acct, "run-a")
    first = notifications.run_escalations(NOW)
    second = notifications.run_escalations(NOW)
    assert (first["alerted"], second["alerted"]) == (1, 0)
    assert len(ns.list_alerts(kind=ns.ALERT_RFQ_ESCALATION)) == 1


# ---------------------------------------------------------------------------
# Flag OFF
# ---------------------------------------------------------------------------

def test_flag_off_lists_no_alerts_and_runs_no_digest(tmp_path, monkeypatch):
    isolate_notification_stores(tmp_path, monkeypatch, notifications_on=False)
    assert notifications.list_open_alerts() == []
    assert notifications.run_concierge_digest() == {"count": 0, "alerts": []}
