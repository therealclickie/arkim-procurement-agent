"""
Arc 4 T8 / D5+D6 — the escalation ladder and its scheduler entry point.

THE TABLE IS THE POINT
----------------------
``decide_escalation`` is a pure function over (age, seen, clicked, opened,
reminded, escalated, state, deferred). It is tested as a table because that is
the only way to assert the row the brief singles out: **OPENED but neither
viewed nor clicked must still escalate.** Pixel opens are fired for everyone by
Apple Mail Privacy Protection, so if an open counted as "seen" the ladder would
switch itself off for a large share of real suppliers — the precise failure this
product exists to prevent.

THE SCHEDULER IS A PLAIN FUNCTION
---------------------------------
``run_escalations(now)`` takes the instant as an argument and is idempotent for
a given ``now``. There is no timer, no thread and no scheduler library anywhere
in the arc (GATE RULINGS / reviewer R8) — and the last test in this file asserts
that structurally rather than trusting a comment.
"""
from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from utils import notifications, notifications_store as ns
from utils.procurement_agent.tests._arc4_notifications_fixtures import (  # noqa: F401
    allowlist, install_fake_provider, isolate_notification_stores,
)

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def ladder(tmp_path, monkeypatch):
    """Arc-4 stores + flags, the real governance gate open for dxpe.com, and a
    fake transport so reminder mail is observable without a network."""
    isolate_notification_stores(tmp_path, monkeypatch)
    monkeypatch.setenv("SEND_GOVERNANCE_V1", "1")
    provider = install_fake_provider(monkeypatch)
    allowlist("dxpe.com")
    return provider


def notification_row(*, age_hours: float = 0.0, clicked: bool = False,
                     opened: bool = False, reminded: bool = False,
                     escalated: bool = False, deferred: bool = False,
                     state: str = ns.STATE_SENT,
                     kind: str = ns.KIND_RFQ_NEW,
                     run_id: str = "run-1") -> dict:
    """A notification dict shaped exactly as the store returns one. Built by
    hand so the decision function can be tested with no store and no clock."""
    sent_at = (NOW - timedelta(hours=age_hours)).isoformat()
    return {
        "id": "n-1", "kind": kind, "state": state, "run_id": run_id,
        "member_id": "m-1", "supplier_domain": "dxpe.com",
        "recipient": "sales@dxpe.com", "created_at": sent_at, "sent_at": sent_at,
        "opened_at": sent_at if opened else None,
        "clicked_at": sent_at if clicked else None,
        "reminded_at": sent_at if reminded else None,
        "escalated_at": sent_at if escalated else None,
        "deferred": 1 if deferred else 0,
    }


def decide(row: dict) -> str | None:
    return notifications.decide_escalation(row, now=NOW, remind_after=4.0,
                                           alert_after=24.0)


# ---------------------------------------------------------------------------
# The table (D6 thresholds 4h / 24h)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,kwargs,viewed,expected", [
    # nothing is due yet
    ("fresh",                         dict(age_hours=0.5),                 False, None),
    ("just_under_the_reminder",       dict(age_hours=3.99),                False, None),
    # the reminder rung
    ("at_the_reminder_threshold",     dict(age_hours=4.0),                 False, "remind"),
    ("past_the_reminder",             dict(age_hours=6.0),                 False, "remind"),
    ("already_reminded",              dict(age_hours=6.0, reminded=True),  False, None),
    # THE row the brief singles out: an open is not a sighting
    ("opened_only_still_reminds",     dict(age_hours=6.0, opened=True),    False, "remind"),
    ("opened_only_still_alerts",      dict(age_hours=30.0, opened=True),   False, "alert"),
    # seen, by either of D5's two real signals
    ("clicked_is_seen",               dict(age_hours=6.0, clicked=True),   False, None),
    ("viewed_in_portal_is_seen",      dict(age_hours=6.0),                 True,  None),
    ("viewed_beats_the_alert_too",    dict(age_hours=30.0),                True,  None),
    # the alert rung, and its precedence over a reminder
    ("at_the_alert_threshold",        dict(age_hours=24.0),                False, "alert"),
    ("late_first_pass_alerts_not_reminds",
                                      dict(age_hours=30.0),                False, "alert"),
    ("reminded_then_unseen_alerts",   dict(age_hours=30.0, reminded=True),  False, "alert"),
    ("already_escalated_is_done",     dict(age_hours=48.0, escalated=True), False, None),
    # states and kinds that are not in the ladder at all
    ("bounced_is_not_a_channel",      dict(age_hours=30.0, state=ns.STATE_BOUNCED),   False, None),
    ("complained_is_not_a_channel",   dict(age_hours=30.0, state=ns.STATE_COMPLAINED), False, None),
    ("suppressed_is_not_a_channel",   dict(age_hours=30.0, state=ns.STATE_SUPPRESSED), False, None),
    ("deferred_belongs_to_the_digest", dict(age_hours=30.0, deferred=True), False, None),
    ("tier1_fyi_never_escalates",     dict(age_hours=30.0, kind=ns.KIND_TIER1_FYI),   False, None),
    ("a_reminder_does_not_ladder",    dict(age_hours=30.0, kind=ns.KIND_RFQ_REMINDER), False, None),
    ("a_digest_does_not_ladder",      dict(age_hours=30.0, kind=ns.KIND_RFQ_DIGEST),  False, None),
])
def test_escalation_decision_table(ladder, label, kwargs, viewed, expected):
    row = notification_row(**kwargs)
    if viewed:
        ns.record_rfq_view(run_id=row["run_id"], supplier_domain="dxpe.com",
                           member_id=row["member_id"], is_test=True)
    assert decide(row) == expected, label


def test_an_open_alone_is_never_seen(ladder):
    """The same fact stated as its own test, because it is the one the reviewer
    is told to look for (R7) and the one a future refactor is most likely to
    break by "simplifying" is_seen into a state check."""
    row = notification_row(age_hours=30.0, opened=True, state=ns.STATE_OPENED)
    assert notifications.is_seen(row) is False
    assert decide(row) == "alert"


def test_the_thresholds_are_configurable(ladder, monkeypatch):
    monkeypatch.setenv("ESCALATE_REMIND_HOURS", "1")
    monkeypatch.setenv("ESCALATE_ALERT_HOURS", "2")
    assert (notifications.remind_hours(), notifications.alert_hours()) == (1.0, 2.0)
    monkeypatch.setenv("ESCALATE_REMIND_HOURS", "not-a-number")
    assert notifications.remind_hours() == notifications.DEFAULT_REMIND_HOURS
    monkeypatch.delenv("ESCALATE_ALERT_HOURS")
    assert notifications.alert_hours() == notifications.DEFAULT_ALERT_HOURS


# ---------------------------------------------------------------------------
# run_escalations over the real store
# ---------------------------------------------------------------------------

def aged_notification(*, hours: float, run_id: str = "run-1",
                      recipient: str = "sales@dxpe.com") -> dict:
    """A real stored RFQ_NEW whose ``sent_at`` is ``hours`` in the past.

    ``transition(..., at=)`` back-dates the send, which is what the ladder
    measures age from — no sleeping, no clock patching.
    """
    n = ns.create_notification(kind=ns.KIND_RFQ_NEW, account_id="acct-1",
                               member_id="m-1", run_id=run_id,
                               supplier_domain="dxpe.com", recipient=recipient,
                               subject_ref="sm-1", is_test=True)
    assert n is not None
    at = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    ns.transition(n["id"], ns.STATE_SENT, event_type="Send", at=at)
    return ns.get_notification(n["id"])


def test_a_reminder_is_sent_once_and_only_once(ladder):
    """D6's hard guarantee. Three runs, one reminder — and the guard is the
    ``reminded_at IS NULL`` write, not a check-then-act, so two overlapping
    schedulers cannot both win."""
    parent = aged_notification(hours=5)
    first = notifications.run_escalations()
    assert first["reminded"] == 1
    assert notifications.run_escalations()["reminded"] == 0
    assert notifications.run_escalations()["reminded"] == 0

    reminders = ns.list_notifications(kind=ns.KIND_RFQ_REMINDER)
    assert len(reminders) == 1
    assert reminders[0]["run_id"] == parent["run_id"]
    assert reminders[0]["member_id"] == parent["member_id"]
    assert ns.get_notification(parent["id"])["reminded_at"]
    assert len(ladder.outbox) == 1
    assert "Reminder" in ladder.outbox[0]["subject"]
    assert ladder.outbox[0]["to"] == ["sales@dxpe.com"]


def test_the_reminder_goes_to_the_same_member_about_the_same_rfq(ladder):
    a = aged_notification(hours=5, run_id="run-a", recipient="a@dxpe.com")
    b = aged_notification(hours=5, run_id="run-b", recipient="b@dxpe.com")
    notifications.run_escalations()
    pairs = {(r["run_id"], r["recipient"])
             for r in ns.list_notifications(kind=ns.KIND_RFQ_REMINDER)}
    assert pairs == {("run-a", "a@dxpe.com"), ("run-b", "b@dxpe.com")}
    assert {a["run_id"], b["run_id"]} == {"run-a", "run-b"}


def test_the_alert_is_raised_once_and_sends_no_further_supplier_mail(ladder):
    """The 24h rung: a human takes over. Two unanswered mails is the point to
    hand off, not the point to send a third."""
    parent = aged_notification(hours=30)
    result = notifications.run_escalations()
    assert (result["alerted"], result["reminded"]) == (1, 0)
    assert ladder.outbox == [], "escalation must not email the supplier"

    alerts = ns.list_alerts(kind=ns.ALERT_RFQ_ESCALATION)
    assert len(alerts) == 1
    assert alerts[0]["notification_id"] == parent["id"]
    assert alerts[0]["run_id"] == parent["run_id"]
    assert alerts[0]["status"] == ns.ALERT_OPEN

    again = notifications.run_escalations()
    assert (again["alerted"], again["reminded"]) == (0, 0)
    assert len(ns.list_alerts(kind=ns.ALERT_RFQ_ESCALATION)) == 1
    assert ladder.outbox == []


def test_a_reminder_then_an_alert_walks_the_whole_ladder(ladder):
    """The intended sequence, driven by two runs at two different instants."""
    parent = aged_notification(hours=5)
    now = datetime.now(timezone.utc)
    assert notifications.run_escalations(now)["reminded"] == 1
    assert notifications.run_escalations(now + timedelta(hours=20))["alerted"] == 1
    after = ns.get_notification(parent["id"])
    assert after["reminded_at"] and after["escalated_at"]
    assert len(ns.list_alerts(kind=ns.ALERT_RFQ_ESCALATION)) == 1
    assert len(ladder.outbox) == 1, "only the reminder was mailed"


def test_running_twice_with_the_same_now_changes_nothing(ladder):
    """Idempotency, stated the way the brief states it.

    ``now`` is read from the real clock, not the module-level ``NOW``:
    :func:`aged_notification` back-dates ``sent_at`` relative to the wall
    clock, so pinning the run instant to a fixed calendar date would make
    the rung a row lands on depend on the day the suite runs.
    """
    aged_notification(hours=5, run_id="run-remind")
    aged_notification(hours=30, run_id="run-alert")
    now = datetime.now(timezone.utc)
    first = notifications.run_escalations(now)
    second = notifications.run_escalations(now)
    assert (first["reminded"], first["alerted"]) == (1, 1)
    assert (second["reminded"], second["alerted"]) == (0, 0)
    assert len(ns.list_notifications(kind=ns.KIND_RFQ_REMINDER)) == 1
    assert len(ns.list_alerts(kind=ns.ALERT_RFQ_ESCALATION)) == 1


def test_a_portal_view_between_runs_stops_the_ladder(ladder):
    """The whole product point: the supplier looked, so nobody is chased."""
    parent = aged_notification(hours=30)
    ns.record_rfq_view(run_id=parent["run_id"], supplier_domain="dxpe.com",
                       member_id="m-1", is_test=True)
    result = notifications.run_escalations()
    assert (result["reminded"], result["alerted"]) == (0, 0)
    assert ns.list_alerts(status=None) == []


def test_a_suppressed_member_is_not_chased(ladder):
    """D8 crossing D6: mail is not a working channel for this address, so the
    ladder does not keep trying it."""
    parent = aged_notification(hours=30)
    ns.transition(parent["id"], ns.STATE_BOUNCED, event_type="Bounce")
    result = notifications.run_escalations()
    assert (result["reminded"], result["alerted"]) == (0, 0)


def test_the_flag_off_scheduler_is_a_noop(ladder, monkeypatch):
    aged_notification(hours=30)
    monkeypatch.setenv("NOTIFICATIONS_V1", "")
    assert notifications.run_escalations() == {"reminded": 0, "alerted": 0,
                                               "considered": 0}
    assert ns.list_alerts(status=None) == []


def test_a_store_failure_degrades_rather_than_raising(ladder, monkeypatch):
    """A scheduler that raises is a cron entry that pages someone at 3am for a
    tracking gap. It reports zeroes instead."""
    def boom(**kwargs):
        raise RuntimeError("store down")

    monkeypatch.setattr(ns, "list_notifications", boom)
    assert notifications.run_escalations()["considered"] == 0


# ---------------------------------------------------------------------------
# The CLI entry point (GATE RULINGS: a plain function + a CLI, nothing else)
# ---------------------------------------------------------------------------

def test_the_cli_runs_the_ladder_and_reports(ladder, capsys):
    from scripts import notifications_scheduler as cli
    aged_notification(hours=5)
    assert cli.main(["escalations"]) == 0
    out = capsys.readouterr().out
    assert "reminded=1" in out
    assert len(ns.list_notifications(kind=ns.KIND_RFQ_REMINDER)) == 1


def test_the_cli_emits_json_and_accepts_an_explicit_now(ladder, capsys):
    import json
    from scripts import notifications_scheduler as cli
    aged_notification(hours=5)
    future = (datetime.now(timezone.utc) + timedelta(hours=40)).isoformat()
    assert cli.main(["escalations", "--now", future, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["command"] == "escalations"
    assert payload["alerted"] == 1, "an explicit --now drives the decision"


def test_the_cli_parses_a_naive_now_as_utc():
    from scripts import notifications_scheduler as cli
    parsed = cli.parse_now("2026-09-21T09:00:00")
    assert parsed == datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)
    assert cli.parse_now(None) is None
    assert cli.parse_now("2026-09-21T09:00:00+02:00").utcoffset() == timedelta(0)


def test_the_cli_says_so_when_the_flag_is_off(ladder, monkeypatch, capsys):
    from scripts import notifications_scheduler as cli
    monkeypatch.setenv("NOTIFICATIONS_V1", "")
    assert cli.main(["escalations"]) == 0
    assert "NOTIFICATIONS_V1 is off" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Reviewer R8, as a test rather than a promise
# ---------------------------------------------------------------------------

def test_no_in_process_timer_or_scheduler_library_was_introduced():
    """GATE RULINGS forbid a background thread, APScheduler, Celery or any
    in-process timer. A timer inside the API process would fire once per worker
    (so "at most one reminder" would depend on the worker count), would stop
    silently on restart, and could not be tested without sleeping.

    The check is over the AST, not the text: the modules' own docstrings NAME
    these libraries to explain why they are absent, and a grep would flag the
    explanation as the offence.
    """
    root = Path(__file__).resolve().parents[3]
    banned_modules = {"apscheduler", "celery", "threading", "_thread", "sched",
                      "multiprocessing", "concurrent.futures", "schedule"}
    banned_calls = {"Timer", "Thread", "create_task", "call_later"}
    arc4_sources = [
        root / "utils" / "notifications.py",
        root / "utils" / "notifications_store.py",
        root / "utils" / "mail_provider.py",
        root / "utils" / "ses_webhook.py",
        root / "scripts" / "notifications_scheduler.py",
    ]
    for path in arc4_sources:
        assert path.exists(), path
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name.split(".")[0] for alias in node.names}
                assert not (names & banned_modules), f"{path.name}: {names}"
            elif isinstance(node, ast.ImportFrom):
                mod = (node.module or "").split(".")[0]
                assert mod not in banned_modules, f"{path.name}: {node.module}"
            elif isinstance(node, ast.Call):
                target = node.func
                name = getattr(target, "attr", None) or getattr(target, "id", None)
                assert name not in banned_calls, f"{path.name}: {name}()"
