"""
Arc 4 T1 — the notification persistence layer.

What these tests pin, in the brief's words: "round-trips; monotonic state
ladder enforced; absorbing terminal states; idempotency key
(provider_message_id + event_type) unique".

``can_transition`` is a pure predicate, so the ladder is asserted as a TABLE
over every (from, to) pair rather than through the store — the rule is the
specification, and a table makes a future change to it visible as a diff.
"""
from __future__ import annotations

import pytest

from utils import notifications_store as ns
from utils.procurement_agent.tests._arc4_notifications_fixtures import (  # noqa: F401
    notif_stores,
)


# ---------------------------------------------------------------------------
# The ladder (D3) — pure, no I/O
# ---------------------------------------------------------------------------

LADDER = (ns.STATE_QUEUED, ns.STATE_SENT, ns.STATE_DELIVERED,
          ns.STATE_OPENED, ns.STATE_CLICKED)


@pytest.mark.parametrize("current,nxt", [
    (ns.STATE_QUEUED, ns.STATE_SENT),
    (ns.STATE_QUEUED, ns.STATE_DELIVERED),     # skipping ahead is still forward
    (ns.STATE_SENT, ns.STATE_DELIVERED),
    (ns.STATE_DELIVERED, ns.STATE_OPENED),
    (ns.STATE_OPENED, ns.STATE_CLICKED),
    (ns.STATE_DELIVERED, ns.STATE_CLICKED),    # SES can deliver a click with no open
])
def test_ladder_allows_forward_moves(current, nxt):
    assert ns.can_transition(current, nxt) is True


@pytest.mark.parametrize("current,nxt", [
    (ns.STATE_SENT, ns.STATE_QUEUED),
    (ns.STATE_DELIVERED, ns.STATE_SENT),
    (ns.STATE_CLICKED, ns.STATE_OPENED),       # a late Open never undoes a Click
    (ns.STATE_OPENED, ns.STATE_OPENED),        # a replay is not progress
])
def test_ladder_refuses_backward_and_repeat_moves(current, nxt):
    assert ns.can_transition(current, nxt) is False


@pytest.mark.parametrize("terminal", sorted(ns.TERMINAL_STATES))
@pytest.mark.parametrize("target", LADDER + tuple(sorted(ns.TERMINAL_STATES)))
def test_terminal_states_absorb(terminal, target):
    """Nothing leaves a terminal state — not a ladder step, not another
    terminal state."""
    assert ns.can_transition(terminal, target) is False


@pytest.mark.parametrize("current", LADDER)
@pytest.mark.parametrize("terminal", sorted(ns.TERMINAL_STATES))
def test_terminal_states_reachable_from_any_progress_state(current, terminal):
    assert ns.can_transition(current, terminal) is True


def test_unknown_states_are_refused():
    assert ns.can_transition(ns.STATE_QUEUED, "NONSENSE") is False
    assert ns.can_transition("NONSENSE", ns.STATE_SENT) is False


def test_creation_only_from_nothing_to_queued():
    assert ns.can_transition(None, ns.STATE_QUEUED) is True
    assert ns.can_transition(None, ns.STATE_SENT) is False


# ---------------------------------------------------------------------------
# Notification round-trips
# ---------------------------------------------------------------------------

def test_create_round_trips_and_starts_queued(notif_stores):
    n = ns.create_notification(kind=ns.KIND_RFQ_NEW, account_id="a1",
                               member_id="m1", subject_ref="sm-1",
                               run_id="run-1", supplier_domain="dxpe.com",
                               recipient="sales@dxpe.com", is_test=True)
    assert n is not None
    assert n["state"] == ns.STATE_QUEUED
    assert n["queued_at"]
    assert n["channel"] == "EMAIL"
    assert ns.get_notification(n["id"]) == n
    # The creation itself is audited (D3: "every transition writes an audit row").
    events = ns.list_events(n["id"])
    assert [e["to_state"] for e in events] == [ns.STATE_QUEUED]


def test_create_refuses_an_unknown_kind(notif_stores):
    assert ns.create_notification(kind="NOT_A_KIND") is None


def test_transition_advances_state_and_stamps_its_column(notif_stores):
    n = ns.create_notification(kind=ns.KIND_RFQ_NEW, is_test=True)
    out = ns.transition(n["id"], ns.STATE_SENT)
    assert out["state"] == ns.STATE_SENT and out["sent_at"]
    out = ns.transition(out["id"], ns.STATE_DELIVERED)
    assert out["state"] == ns.STATE_DELIVERED and out["delivered_at"]


def test_transition_refused_backwards_still_audits_and_leaves_state(notif_stores):
    n = ns.create_notification(kind=ns.KIND_RFQ_NEW, is_test=True)
    ns.transition(n["id"], ns.STATE_DELIVERED)
    out = ns.transition(n["id"], ns.STATE_SENT)
    assert out["state"] == ns.STATE_DELIVERED           # unchanged
    refused = [e for e in ns.list_events(n["id"]) if e["applied"] == 0]
    assert len(refused) == 1 and refused[0]["to_state"] == ns.STATE_SENT


def test_terminal_state_absorbs_through_the_store(notif_stores):
    n = ns.create_notification(kind=ns.KIND_RFQ_NEW, is_test=True)
    ns.transition(n["id"], ns.STATE_BOUNCED)
    out = ns.transition(n["id"], ns.STATE_DELIVERED)
    assert out["state"] == ns.STATE_BOUNCED


def test_a_click_after_a_terminal_state_still_stamps_clicked_at(notif_stores):
    """Invariant 4: the ladder governs ``state``; a timestamp records a real
    human action even when the state cannot move. D5 reads ``clicked_at``."""
    n = ns.create_notification(kind=ns.KIND_RFQ_NEW, is_test=True)
    ns.transition(n["id"], ns.STATE_BOUNCED)
    out = ns.transition(n["id"], ns.STATE_CLICKED)
    assert out["state"] == ns.STATE_BOUNCED
    assert out["clicked_at"] is not None


def test_transition_of_unknown_notification_is_none(notif_stores):
    assert ns.transition("no-such-id", ns.STATE_SENT) is None


def test_list_notifications_filters(notif_stores):
    a = ns.create_notification(kind=ns.KIND_RFQ_NEW, member_id="m1", is_test=True)
    ns.create_notification(kind=ns.KIND_RFQ_REMINDER, member_id="m2", is_test=True)
    ns.create_notification(kind=ns.KIND_RFQ_NEW, member_id="m2", deferred=True,
                           is_test=True)
    assert [n["id"] for n in ns.list_notifications(member_id="m1")] == [a["id"]]
    assert len(ns.list_notifications(kind=ns.KIND_RFQ_NEW)) == 2
    assert len(ns.list_notifications(kind=ns.KIND_RFQ_NEW, deferred=True)) == 1
    assert len(ns.list_notifications(kinds=[ns.KIND_RFQ_NEW,
                                            ns.KIND_RFQ_REMINDER])) == 3


def test_provider_message_id_binds_and_resolves(notif_stores):
    n = ns.create_notification(kind=ns.KIND_RFQ_NEW, is_test=True)
    assert ns.set_provider_message_id(n["id"], "ses-abc", provider="ses") is True
    found = ns.get_notification_by_provider_message_id("ses-abc")
    assert found["id"] == n["id"] and found["provider"] == "ses"
    assert ns.get_notification_by_provider_message_id("nope") is None


# ---------------------------------------------------------------------------
# D4 idempotency key
# ---------------------------------------------------------------------------

def test_provider_event_claim_is_unique_on_message_id_plus_type(notif_stores):
    assert ns.claim_provider_event("ses-1", "Delivery") is True
    assert ns.claim_provider_event("ses-1", "Delivery") is False   # replay
    assert ns.claim_provider_event("ses-1", "Open") is True        # different type
    assert ns.claim_provider_event("ses-2", "Delivery") is True    # different message


def test_claim_refuses_empty_inputs(notif_stores):
    assert ns.claim_provider_event("", "Delivery") is False
    assert ns.claim_provider_event("ses-1", "") is False


def test_local_events_without_a_provider_id_are_not_deduplicated(notif_stores):
    """The idempotency index is PARTIAL on purpose: two locally-generated
    QUEUED rows for two different notifications must both be writable."""
    a = ns.create_notification(kind=ns.KIND_RFQ_NEW, is_test=True)
    b = ns.create_notification(kind=ns.KIND_RFQ_NEW, is_test=True)
    assert len(ns.list_events(a["id"])) == 1
    assert len(ns.list_events(b["id"])) == 1


# ---------------------------------------------------------------------------
# One-reminder / one-escalation guards (D6)
# ---------------------------------------------------------------------------

def test_mark_reminded_succeeds_once(notif_stores):
    n = ns.create_notification(kind=ns.KIND_RFQ_NEW, is_test=True)
    assert ns.mark_reminded(n["id"]) is True
    assert ns.mark_reminded(n["id"]) is False
    assert ns.get_notification(n["id"])["reminded_at"] is not None


def test_mark_escalated_succeeds_once(notif_stores):
    n = ns.create_notification(kind=ns.KIND_RFQ_NEW, is_test=True)
    assert ns.mark_escalated(n["id"]) is True
    assert ns.mark_escalated(n["id"]) is False


# ---------------------------------------------------------------------------
# RfqView (D5)
# ---------------------------------------------------------------------------

def test_first_view_sets_both_timestamps_then_only_last_moves(notif_stores):
    first = ns.record_rfq_view(run_id="run-1", supplier_domain="dxpe.com",
                               member_id="m1", at="2026-09-20T10:00:00+00:00",
                               is_test=True)
    assert first["first_viewed_at"] == first["last_viewed_at"]
    assert first["view_count"] == 1
    second = ns.record_rfq_view(run_id="run-1", supplier_domain="dxpe.com",
                                member_id="m1", at="2026-09-20T11:00:00+00:00",
                                is_test=True)
    assert second["first_viewed_at"] == "2026-09-20T10:00:00+00:00"
    assert second["last_viewed_at"] == "2026-09-20T11:00:00+00:00"
    assert second["view_count"] == 2


def test_token_door_view_records_with_a_null_member(notif_stores):
    """D5: the claim-token door has no member — the row still exists, keyed
    on the domain. SQLite treats NULLs as distinct, so this is exactly the
    case ``member_key`` exists to make idempotent."""
    ns.record_rfq_view(run_id="run-1", supplier_domain="dxpe.com", is_test=True)
    ns.record_rfq_view(run_id="run-1", supplier_domain="dxpe.com", is_test=True)
    row = ns.get_rfq_view(run_id="run-1", supplier_domain="dxpe.com")
    assert row["member_id"] is None and row["view_count"] == 2


def test_view_by_a_colleague_counts_as_the_company_having_seen_it(notif_stores):
    ns.record_rfq_view(run_id="run-1", supplier_domain="dxpe.com",
                       member_id="colleague", is_test=True)
    assert ns.rfq_viewed("run-1", member_id="m1",
                         supplier_domain="dxpe.com") is True
    assert ns.rfq_viewed("run-2", supplier_domain="dxpe.com") is False


def test_viewed_run_ids_is_scoped_to_the_domain(notif_stores):
    ns.record_rfq_view(run_id="run-1", supplier_domain="dxpe.com", is_test=True)
    ns.record_rfq_view(run_id="run-2", supplier_domain="other.com", is_test=True)
    assert ns.viewed_run_ids("dxpe.com") == {"run-1"}


# ---------------------------------------------------------------------------
# Preferences (D7)
# ---------------------------------------------------------------------------

def test_preference_defaults_to_immediate_and_round_trips(notif_stores):
    assert ns.get_preference("m1") == ns.PREF_IMMEDIATE
    assert ns.set_preference("m1", ns.PREF_DAILY_DIGEST, account_id="a1") == \
        ns.PREF_DAILY_DIGEST
    assert ns.get_preference("m1") == ns.PREF_DAILY_DIGEST
    assert ns.set_preference("m1", ns.PREF_NONE) == ns.PREF_NONE
    assert ns.get_preference("m1") == ns.PREF_NONE


def test_unknown_preference_is_refused(notif_stores):
    assert ns.set_preference("m1", "SOMETIMES") is None
    assert ns.get_preference("m1") == ns.PREF_IMMEDIATE


# ---------------------------------------------------------------------------
# Concierge alerts
# ---------------------------------------------------------------------------

def test_alert_dedupes_on_key_and_acknowledge_is_a_status_flip(notif_stores):
    a = ns.raise_alert(kind=ns.ALERT_RFQ_ESCALATION, dedupe_key="n-1",
                       run_id="run-1", is_test=True)
    assert a is not None and a["status"] == ns.ALERT_OPEN
    assert ns.raise_alert(kind=ns.ALERT_RFQ_ESCALATION, dedupe_key="n-1") is None
    assert len(ns.list_alerts()) == 1

    out = ns.acknowledge_alert(a["id"], acknowledged_by="admin")
    assert out["status"] == ns.ALERT_ACKNOWLEDGED and out["acknowledged_by"] == "admin"
    assert ns.list_alerts(status=ns.ALERT_OPEN) == []
    # Never a delete — the row is still there, auditable.
    assert ns.get_alert(a["id"]) is not None
    assert ns.acknowledge_alert(a["id"], acknowledged_by="admin") is None


def test_alerts_without_a_dedupe_key_are_always_distinct(notif_stores):
    assert ns.raise_alert(kind=ns.ALERT_EMAIL_SUPPRESSED, is_test=True) is not None
    assert ns.raise_alert(kind=ns.ALERT_EMAIL_SUPPRESSED, is_test=True) is not None
    assert len(ns.list_alerts()) == 2


# ---------------------------------------------------------------------------
# Address-level suppression + soft bounces (D8 / gate F3)
# ---------------------------------------------------------------------------

def test_suppression_is_address_level_and_case_insensitive(notif_stores):
    assert ns.suppress_email("Sales@DXPE.com", reason="hard_bounce") is True
    assert ns.is_email_suppressed("sales@dxpe.com") is True
    # The colleague at the same DOMAIN is untouched (gate FINDING F3).
    assert ns.is_email_suppressed("other@dxpe.com") is False
    assert [r["email"] for r in ns.list_suppressed_emails()] == ["sales@dxpe.com"]


def test_soft_bounce_counter_increments_and_resets(notif_stores):
    assert ns.bump_soft_bounce("a@b.com") == 1
    assert ns.bump_soft_bounce("a@b.com") == 2
    assert ns.soft_bounce_count("a@b.com") == 2
    assert ns.reset_soft_bounce("a@b.com") is True
    assert ns.soft_bounce_count("a@b.com") == 0
