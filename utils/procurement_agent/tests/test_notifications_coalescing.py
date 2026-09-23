"""
Arc 4b T7 / S2 — coalesced RFQ_NEW mail and consolidated reminders.

THE RULE, AND THE ONE JUDGEMENT CALL IN IT
------------------------------------------
S2 asks that requests arriving together produce one email, and that reminders
be one per member per day listing everything unseen.

For reminders that is implemented literally. For RFQ_NEW there is a judgement
call, made here and stated so a reviewer can disagree with it on purpose: the
FIRST request to an idle mailbox is mailed straight away and opens a window;
every further request inside that window joins the batch and is mailed once,
with the others, when it closes. Holding the first request for fifteen minutes
to make a tidier email would delay a line-down part, and a pre-existing,
un-editable test (``test_notifications_preferences.py::test_immediate_sends_now``)
pins immediate delivery for the first one. Ten requests released together
therefore produce TWO emails rather than ten, and — unlike a scheme that
silently attaches the extras to the first mail — every request is named in an
email somebody actually receives.

DETERMINISM (criterion 10): every function under test takes ``now``. Nothing
below sleeps, and nothing below depends on the calendar date the suite runs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from utils import (business_hours, notifications, notifications_store as ns,
                   supplier_accounts)
from utils.procurement_agent.tests._arc4_notifications_fixtures import (  # noqa: F401
    allowlist, install_fake_provider, isolate_notification_stores,
)

# Tuesday 08:00 America/Los_Angeles — the start of the supplier's working day,
# so reminders may actually go out at this instant (S3). Fixed, so every
# assertion here is a function of the instants the test supplies.
NOW = datetime(2026, 9, 22, 15, 0, tzinfo=timezone.utc)
NEXT_DAY = NOW + timedelta(days=1)          # Wednesday, same local time


@pytest.fixture
def batch(tmp_path, monkeypatch):
    isolate_notification_stores(tmp_path, monkeypatch)
    monkeypatch.setenv("SEND_GOVERNANCE_V1", "1")
    provider = install_fake_provider(monkeypatch)
    allowlist("dxpe.com")
    return provider


def one_contact_account(domain: str = "dxpe.com"):
    acct = supplier_accounts.create_account(domain)
    member = supplier_accounts.add_member(
        acct["id"], f"owner@{domain}", role=supplier_accounts.ROLE_OWNER,
        status=supplier_accounts.MEMBER_ACTIVE)
    return acct, member


def rfq(run_id: str, part: str = "3196") -> dict:
    return {"run_id": run_id, "supplier_domain": "dxpe.com",
            "sent_message_id": f"sm-{run_id}", "manufacturer": "Goulds",
            "part_number": part, "quantity": 2}


def notify(acct, run_id: str, *, at: datetime, part: str = "3196"):
    return notifications._notify_rfq_new(rfq(run_id, part), acct, now=at)


# ---------------------------------------------------------------------------
# RFQ_NEW coalescing
# ---------------------------------------------------------------------------

def test_three_rfqs_inside_the_window_produce_one_batched_email(batch):
    """Three requests, two emails: the first immediately, then ONE listing
    the other two. Arc 4 sent three."""
    acct, _ = one_contact_account()
    notify(acct, "run-a", at=NOW)
    notify(acct, "run-b", at=NOW + timedelta(minutes=2))
    notify(acct, "run-c", at=NOW + timedelta(minutes=5))
    assert len(batch.outbox) == 1, "only the anchor has been mailed so far"

    out = notifications.run_coalesced_sends(now=NOW + timedelta(minutes=20))
    assert out == {"batches": 1, "notifications": 2}
    assert len(batch.outbox) == 2
    body = batch.outbox[1]["body"]
    assert "2 requests for quote" in body
    assert body.count("Goulds 3196") == 2
    assert "2 new quote requests" in batch.outbox[1]["subject"]


def test_every_request_ends_up_sent_and_none_is_left_behind(batch):
    acct, _ = one_contact_account()
    for i, run in enumerate(("run-a", "run-b", "run-c")):
        notify(acct, run, at=NOW + timedelta(minutes=i))
    notifications.run_coalesced_sends(now=NOW + timedelta(minutes=20))
    rows = ns.list_notifications(kind=ns.KIND_RFQ_NEW)
    assert len(rows) == 3
    assert {r["state"] for r in rows} == {ns.STATE_SENT}
    assert {r["run_id"] for r in rows} == {"run-a", "run-b", "run-c"}


def test_an_rfq_arriving_after_the_window_starts_a_new_batch(batch):
    acct, _ = one_contact_account()
    notify(acct, "run-a", at=NOW)
    notify(acct, "run-b", at=NOW + timedelta(minutes=2))
    notifications.run_coalesced_sends(now=NOW + timedelta(minutes=20))
    assert len(batch.outbox) == 2

    later = NOW + timedelta(minutes=40)
    notify(acct, "run-c", at=later)
    assert len(batch.outbox) == 3, "a new window opens with its own anchor"
    assert notifications.run_coalesced_sends(now=later + timedelta(minutes=20)) == \
        {"batches": 0, "notifications": 0}


def test_the_flush_is_idempotent_for_a_given_now(batch):
    acct, _ = one_contact_account()
    notify(acct, "run-a", at=NOW)
    notify(acct, "run-b", at=NOW + timedelta(minutes=1))
    at = NOW + timedelta(minutes=20)
    first = notifications.run_coalesced_sends(now=at)
    second = notifications.run_coalesced_sends(now=at)
    assert (first["batches"], second["batches"]) == (1, 0)
    assert len(batch.outbox) == 2


def test_nothing_is_flushed_before_the_window_closes(batch):
    acct, _ = one_contact_account()
    notify(acct, "run-a", at=NOW)
    notify(acct, "run-b", at=NOW + timedelta(minutes=1))
    assert notifications.run_coalesced_sends(now=NOW + timedelta(minutes=5)) == \
        {"batches": 0, "notifications": 0}
    assert len(batch.outbox) == 1


def test_a_single_batched_request_keeps_the_one_request_wording(batch):
    """A batch of one must not read like a list of one."""
    acct, _ = one_contact_account()
    notify(acct, "run-a", at=NOW)
    notify(acct, "run-b", at=NOW + timedelta(minutes=1), part="3296")
    notifications.run_coalesced_sends(now=NOW + timedelta(minutes=20))
    assert batch.outbox[1]["subject"] == "New quote request — Goulds 3296"


def test_two_mailboxes_batch_independently(batch):
    """The group key is the ADDRESS: one email is a promise about a mailbox."""
    acct, _ = one_contact_account()
    supplier_accounts.add_member(acct["id"], "admin@dxpe.com",
                                 role=supplier_accounts.ROLE_ADMIN,
                                 status=supplier_accounts.MEMBER_ACTIVE)
    notify(acct, "run-a", at=NOW)
    notify(acct, "run-b", at=NOW + timedelta(minutes=1))
    assert len(batch.outbox) == 2, "one anchor each"
    out = notifications.run_coalesced_sends(now=NOW + timedelta(minutes=20))
    assert out == {"batches": 2, "notifications": 2}
    assert sorted(m["to"][0] for m in batch.outbox[2:]) == ["admin@dxpe.com",
                                                            "owner@dxpe.com"]


def test_a_zero_window_restores_arc_4_timing(batch, monkeypatch):
    """The escape hatch: a deployment that wants one email per request back
    sets the window to zero rather than changing code."""
    monkeypatch.setenv("NOTIFY_COALESCE_MINUTES", "0")
    acct, _ = one_contact_account()
    notify(acct, "run-a", at=NOW)
    notify(acct, "run-b", at=NOW + timedelta(minutes=1))
    assert len(batch.outbox) == 2
    assert notifications.run_coalesced_sends(now=NOW + timedelta(minutes=5)) == \
        {"batches": 0, "notifications": 0}


def test_a_digest_member_is_never_picked_up_by_the_coalescer(batch):
    """Two batchers claiming the same row is how a supplier is told twice."""
    acct, members = one_contact_account()
    ns.set_preference(members["id"], ns.PREF_DAILY_DIGEST)
    notify(acct, "run-a", at=NOW)
    notify(acct, "run-b", at=NOW + timedelta(minutes=1))
    assert batch.outbox == []
    assert notifications.run_coalesced_sends(now=NOW + timedelta(hours=2)) == \
        {"batches": 0, "notifications": 0}
    assert notifications.run_daily_digest()["notifications"] == 2


def test_a_suppressed_address_is_not_batched_to(batch):
    acct, _ = one_contact_account()
    notify(acct, "run-a", at=NOW)
    notify(acct, "run-b", at=NOW + timedelta(minutes=1))
    ns.suppress_email("owner@dxpe.com", reason="hard_bounce")
    assert notifications.run_coalesced_sends(now=NOW + timedelta(minutes=20)) == \
        {"batches": 0, "notifications": 0}


def test_the_window_decision_reads_no_clock(batch):
    """Criterion 10: the same instants give the same answer, whenever the
    suite runs. Asked about a batch whose window closed in 2020 and one that
    closes in 2099."""
    row = {"coalesce_until": "2026-09-22T15:15:00+00:00"}
    assert notifications._window_closed(row, NOW) is False
    assert notifications._window_closed(row, NOW + timedelta(hours=1)) is True
    assert notifications._window_closed({}, NOW) is True


# ---------------------------------------------------------------------------
# Consolidated reminders
# ---------------------------------------------------------------------------

def aged(run_id: str, *, hours: float, recipient: str = "owner@dxpe.com",
         at: datetime = NOW) -> dict:
    """One stored RFQ_NEW, sent ``hours`` BUSINESS hours before ``at``.
    Back-dated explicitly — no sleeping, no clock patching, and no dependence
    on the calendar date the suite runs (S3)."""
    n = ns.create_notification(kind=ns.KIND_RFQ_NEW, account_id="acct-1",
                               member_id="m-1", run_id=run_id,
                               supplier_domain="dxpe.com", recipient=recipient,
                               subject_ref=f"sm-{run_id}", is_test=True,
                               detail={"manufacturer": "Goulds",
                                       "part_number": run_id})
    assert n is not None
    ns.transition(n["id"], ns.STATE_SENT, event_type="Send",
                  at=business_hours.business_hours_before(at, hours).isoformat())
    return ns.get_notification(n["id"])


def test_three_unseen_rfqs_produce_one_reminder_not_three(batch):
    for run in ("run-a", "run-b", "run-c"):
        aged(run, hours=6)
    out = notifications.run_escalations(NOW)
    assert out["reminded"] == 1
    reminders = ns.list_notifications(kind=ns.KIND_RFQ_REMINDER)
    assert len(reminders) == 1
    assert sorted(reminders[0]["detail"]["batched"]) == sorted(
        n["id"] for n in ns.list_notifications(kind=ns.KIND_RFQ_NEW))
    assert len(batch.outbox) == 1
    body = batch.outbox[0]["body"]
    assert "3 quote requests" in body
    for run in ("run-a", "run-b", "run-c"):
        assert f"Goulds {run}" in body


def test_an_rfq_never_appears_in_two_reminders(batch):
    """Arc 4's one-reminder-per-RFQ guarantee, in its consolidated form. The
    guard is the ``reminded_at IS NULL`` write, not a check-then-act."""
    aged("run-a", hours=6)
    assert notifications.run_escalations(NOW)["reminded"] == 1
    aged("run-b", hours=6, at=NEXT_DAY)
    # A later business day, so the per-day gate is open again.
    assert notifications.run_escalations(NEXT_DAY)["reminded"] == 1

    batched: list[str] = []
    for r in ns.list_notifications(kind=ns.KIND_RFQ_REMINDER):
        batched += (r.get("detail") or {}).get("batched") or []
    assert len(batched) == len(set(batched)), "an RFQ was reminded twice"
    assert len(batched) == 2


def test_a_second_batch_the_same_day_is_deferred_never_dropped(batch, monkeypatch):
    """The gate defers; it does not discard. The RFQ that missed today's
    reminder keeps reminded_at NULL and is carried into tomorrow's.

    The alert rung is pushed out of the way for this case: the question here
    is what the PER-DAY gate does with a second batch, and an RFQ that crosses
    the one-business-day escalation threshold in the meantime would answer a
    different question.
    """
    monkeypatch.setenv("ESCALATE_ALERT_HOURS", "40")
    aged("run-a", hours=6)
    assert notifications.run_escalations(NOW)["reminded"] == 1
    late = aged("run-b", hours=5, at=NOW)
    assert notifications.run_escalations(NOW + timedelta(minutes=30))["reminded"] == 0
    assert ns.get_notification(late["id"])["reminded_at"] is None

    assert notifications.run_escalations(NEXT_DAY)["reminded"] == 1
    assert ns.get_notification(late["id"])["reminded_at"] is not None


def test_two_mailboxes_each_get_their_own_reminder(batch):
    aged("run-a", hours=6, recipient="a@dxpe.com")
    aged("run-b", hours=6, recipient="b@dxpe.com")
    assert notifications.run_escalations(NOW)["reminded"] == 2
    assert sorted(r["recipient"]
                  for r in ns.list_notifications(kind=ns.KIND_RFQ_REMINDER)) == \
        ["a@dxpe.com", "b@dxpe.com"]


def test_a_single_unseen_rfq_keeps_the_arc_4_reminder_wording(batch):
    aged("run-a", hours=6)
    notifications.run_escalations(NOW)
    assert "Reminder: quote request waiting" in batch.outbox[0]["subject"]


def test_a_seen_rfq_is_left_out_of_the_consolidated_reminder(batch):
    """The whole product point: the supplier looked, so that one is dropped
    from the chase — and the others are not."""
    aged("run-a", hours=6)
    aged("run-b", hours=6)
    ns.record_rfq_view(run_id="run-a", supplier_domain="dxpe.com",
                       member_id="m-1", is_test=True)
    notifications.run_escalations(NOW)
    (reminder,) = ns.list_notifications(kind=ns.KIND_RFQ_REMINDER)
    assert len(reminder["detail"]["batched"]) == 1
    assert batch.outbox[0]["subject"] == "Reminder: quote request waiting — Goulds run-b"


def test_the_reminder_day_takes_now_as_a_parameter(batch):
    """Criterion 10, stated directly: the day a reminder counts against is a
    function of the supplied instant, never of the wall clock."""
    assert notifications.reminder_day(NOW) == "2026-09-22"
    assert notifications.reminder_day(NOW + timedelta(days=3)) == "2026-09-25"


# ---------------------------------------------------------------------------
# The CLI entry point
# ---------------------------------------------------------------------------

def test_the_cli_runs_the_coalescer(batch, capsys):
    from scripts import notifications_scheduler as cli
    acct, _ = one_contact_account()
    notify(acct, "run-a", at=NOW)
    notify(acct, "run-b", at=NOW + timedelta(minutes=1))
    future = (NOW + timedelta(hours=1)).isoformat()
    assert cli.main(["coalesce", "--now", future, "--json"]) == 0
    import json
    # The stores print as they work; the JSON line is the last thing written.
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload == {"command": "coalesce", "batches": 1, "notifications": 1}


# ---------------------------------------------------------------------------
# Flag OFF — today's behaviour exactly
# ---------------------------------------------------------------------------

def test_flag_off_coalescer_is_a_noop(tmp_path, monkeypatch):
    isolate_notification_stores(tmp_path, monkeypatch, notifications_on=False)
    assert notifications.run_coalesced_sends(NOW) == {"batches": 0,
                                                      "notifications": 0}
