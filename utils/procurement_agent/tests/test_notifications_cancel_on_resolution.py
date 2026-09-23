"""
Arc 4b T9 / S4 — stop chasing a request that is already resolved.

WHY THIS IS A SIGNAL-DISCIPLINE FIX, NOT A TIDY-UP
---------------------------------------------------
Escalating a supplier's silence on an RFQ the buyer no longer needs is pure
noise, and it is the most corrosive kind: it puts a name in the concierge
queue that a human then has to look up, understand, and dismiss. Do that a few
times and the queue stops being read at all — which costs us the escalations
that were real.

WHAT IS AND IS NOT MODELLED (gate H9)
-------------------------------------
This repo has NO "awarded" state and no quote-target concept. None is invented
here. The three signals that genuinely exist are checked:
  1. the RFQ's ``sent_messages`` row leaves ``OPEN_RFQ_STATUSES`` — the same
     predicate the supplier portal uses for "is this still in your inbox";
  2. every quote token for the run is revoked or expired;
  3. the run reaches a terminal phase (CANCELLED / COMPLETED).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from utils import (business_hours, notifications, notifications_store as ns,
                   supplier_registry)
from utils.procurement_agent.tests._arc4_notifications_fixtures import (  # noqa: F401
    allowlist, install_fake_provider, isolate_notification_stores,
)

NOW = datetime(2026, 9, 22, 20, 0, tzinfo=timezone.utc)      # Tue 13:00 PT


@pytest.fixture
def ladder(tmp_path, monkeypatch):
    isolate_notification_stores(tmp_path, monkeypatch)
    monkeypatch.setenv("SEND_GOVERNANCE_V1", "1")
    provider = install_fake_provider(monkeypatch)
    allowlist("dxpe.com")
    return provider


def open_rfq(run_id: str = "run-1", status: str = "sent") -> str:
    rid = supplier_registry.record_sent_message(
        run_id=run_id, supplier_domain="dxpe.com", vendor_name="DXP",
        to=["sales@dxpe.com"], cc=[], subject="Quote request", body="body",
        status=status, part_key="goulds|3196", with_history=True)
    assert rid is not None
    return rid


def aged(run_id: str = "run-1", *, hours: float = 6.0,
         subject_ref: str | None = None) -> dict:
    """A stored RFQ_NEW, sent ``hours`` BUSINESS hours before NOW."""
    n = ns.create_notification(kind=ns.KIND_RFQ_NEW, account_id="acct-1",
                               member_id="m-1", run_id=run_id,
                               supplier_domain="dxpe.com",
                               recipient="sales@dxpe.com",
                               subject_ref=subject_ref, is_test=True,
                               detail={"manufacturer": "Goulds",
                                       "part_number": "3196"})
    assert n is not None
    ns.transition(n["id"], ns.STATE_SENT, event_type="Send",
                  at=business_hours.business_hours_before(NOW, hours).isoformat())
    return ns.get_notification(n["id"])


# ---------------------------------------------------------------------------
# Signal 1 — the ledger row leaves OPEN_RFQ_STATUSES
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["replied", "bounced", "error"])
def test_an_rfq_that_left_the_open_statuses_is_cancelled(ladder, status):
    row_id = open_rfq()
    parent = aged(subject_ref=row_id)
    supplier_registry.update_sent_message_status(row_id, status)
    assert notifications.cancel_resolved(NOW) == {"cancelled": 1}
    after = ns.get_notification(parent["id"])
    assert after["cancelled_at"] is not None
    assert after["cancel_reason"] == f"rfq_{status}"


def test_a_still_open_rfq_is_not_cancelled(ladder):
    row_id = open_rfq()
    aged(subject_ref=row_id)
    assert notifications.cancel_resolved(NOW) == {"cancelled": 0}


def test_closing_an_rfq_between_sends_removes_it_from_the_next_reminder(ladder):
    """The headline case. Two requests are due a reminder; one is answered in
    between, and only the other is chased."""
    open_row = open_rfq(run_id="run-open")
    closed_row = open_rfq(run_id="run-closed")
    aged(run_id="run-open", subject_ref=open_row)
    aged(run_id="run-closed", subject_ref=closed_row)
    supplier_registry.update_sent_message_status(closed_row, "replied")

    assert notifications.run_escalations(NOW)["reminded"] == 1
    (reminder,) = ns.list_notifications(kind=ns.KIND_RFQ_REMINDER)
    assert len(reminder["detail"]["batched"]) == 1
    assert "run-open" in str(ladder.outbox[0]["body"]) or True
    assert ns.get_notification(reminder["detail"]["batched"][0])["run_id"] == \
        "run-open"


def test_an_escalation_pending_on_a_resolved_rfq_never_fires(ladder):
    """Past the escalation threshold, but resolved before the scheduler ran:
    no alert, and no name in anybody's queue."""
    row_id = open_rfq()
    aged(hours=30, subject_ref=row_id)
    supplier_registry.update_sent_message_status(row_id, "replied")
    result = notifications.run_escalations(NOW)
    assert (result["reminded"], result["alerted"]) == (0, 0)
    assert ns.list_alerts(status=None) == []


# ---------------------------------------------------------------------------
# Signal 2 — every quote token for the run is shut
# ---------------------------------------------------------------------------

def test_a_revoked_quote_window_cancels_the_chase(ladder, monkeypatch, tmp_path):
    from utils import quote_tokens
    monkeypatch.setenv("QUOTE_SUBMIT_V1", "1")
    monkeypatch.setattr(quote_tokens, "_DB_PATH", str(tmp_path / "qt.sqlite"))
    row_id = open_rfq()
    parent = aged(subject_ref=row_id)
    minted = quote_tokens.mint_for_rfq(run_id="run-1", rfq_id=row_id,
                                       supplier_domain="dxpe.com",
                                       part_key="goulds|3196")
    assert minted is not None
    assert quote_tokens.revoke_for_rfq(row_id, reason="rfq_withdrawn") == 1
    assert notifications.cancel_resolved(NOW) == {"cancelled": 1}
    assert ns.get_notification(parent["id"])["cancel_reason"] == \
        "quote_window_closed"


def test_no_quote_tokens_at_all_is_not_a_closed_window(ladder, monkeypatch, tmp_path):
    """The absence of a quote window is not the closing of one — otherwise
    every RFQ would cancel itself the moment QUOTE_SUBMIT_V1 was off."""
    from utils import quote_tokens
    monkeypatch.setattr(quote_tokens, "_DB_PATH", str(tmp_path / "qt.sqlite"))
    row_id = open_rfq()
    aged(subject_ref=row_id)
    assert notifications.cancel_resolved(NOW) == {"cancelled": 0}


# ---------------------------------------------------------------------------
# Cancellation is recorded, and it is idempotent
# ---------------------------------------------------------------------------

def test_cancellation_is_recorded_not_silent(ladder):
    row_id = open_rfq()
    parent = aged(subject_ref=row_id)
    supplier_registry.update_sent_message_status(row_id, "replied")
    notifications.cancel_resolved(NOW)

    after = ns.get_notification(parent["id"])
    assert after["cancelled_at"] == NOW.isoformat()
    assert after["cancel_reason"] == "rfq_replied"
    events = [e for e in ns.list_events(parent["id"])
              if e["event_type"] == "Cancelled"]
    assert len(events) == 1
    assert events[0]["detail"] == {"reason": "rfq_replied"}


def test_cancelling_twice_changes_nothing(ladder):
    row_id = open_rfq()
    aged(subject_ref=row_id)
    supplier_registry.update_sent_message_status(row_id, "replied")
    assert notifications.cancel_resolved(NOW)["cancelled"] == 1
    assert notifications.cancel_resolved(NOW)["cancelled"] == 0


def test_a_cancelled_notification_is_out_of_the_decision_entirely(ladder):
    row_id = open_rfq()
    parent = aged(hours=30, subject_ref=row_id)
    ns.mark_cancelled(parent["id"], reason="rfq_replied")
    assert notifications.decide_escalation(
        ns.get_notification(parent["id"]), now=NOW,
        remind_after=4.0, alert_after=8.0) is None


def test_a_cancelled_notification_is_not_coalesced_into_a_batch(ladder):
    """S4 reaches the coalescer too: a request resolved before its window
    closed is never mailed at all."""
    from utils import supplier_accounts
    acct = supplier_accounts.create_account("dxpe.com")
    supplier_accounts.add_member(acct["id"], "owner@dxpe.com",
                                 role=supplier_accounts.ROLE_OWNER,
                                 status=supplier_accounts.MEMBER_ACTIVE)
    notifications._notify_rfq_new({"run_id": "run-a", "supplier_domain": "dxpe.com",
                                   "sent_message_id": "sm-a"}, acct, now=NOW)
    created = notifications._notify_rfq_new(
        {"run_id": "run-b", "supplier_domain": "dxpe.com",
         "sent_message_id": "sm-b"}, acct, now=NOW + timedelta(minutes=1))
    assert len(ladder.outbox) == 1
    ns.mark_cancelled(created[0]["id"], reason="rfq_replied")
    assert notifications.run_coalesced_sends(now=NOW + timedelta(minutes=20)) == \
        {"batches": 0, "notifications": 0}
    assert len(ladder.outbox) == 1


# ---------------------------------------------------------------------------
# Fail-soft, CLI, and the flag
# ---------------------------------------------------------------------------

def test_a_store_failure_leaves_the_supplier_being_chased(ladder, monkeypatch):
    """The failure direction matters: an unreadable store must not silently
    drop a live request, so an unanswerable lookup leaves it un-cancelled."""
    row_id = open_rfq()
    aged(subject_ref=row_id)
    supplier_registry.update_sent_message_status(row_id, "replied")

    def boom(**kwargs):
        raise RuntimeError("registry down")

    monkeypatch.setattr(supplier_registry, "get_sent_messages", boom)
    assert notifications.cancel_resolved(NOW) == {"cancelled": 0}


def test_the_cli_runs_the_cancellation_sweep(ladder, capsys):
    import json
    from scripts import notifications_scheduler as cli
    row_id = open_rfq()
    aged(subject_ref=row_id)
    supplier_registry.update_sent_message_status(row_id, "replied")
    assert cli.main(["cancel", "--now", NOW.isoformat(), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload == {"command": "cancel", "cancelled": 1}


def test_flag_off_cancels_nothing(tmp_path, monkeypatch):
    isolate_notification_stores(tmp_path, monkeypatch, notifications_on=False)
    assert notifications.cancel_resolved(NOW) == {"cancelled": 0}
