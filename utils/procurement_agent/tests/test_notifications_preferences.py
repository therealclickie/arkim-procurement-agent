"""
Arc 4 T10 / D7 — member notification preferences and the daily digest.

SELF ONLY, AND SELF IS NOT A PARAMETER
--------------------------------------
``GET``/``PUT /api/supplier/notification-preferences`` take no member id: the
member comes from the validated session. That is stronger than checking an id
against the session, because there is nothing to check — a cross-member write is
not rejected, it is unrepresentable. The tests below assert the property that
matters (one member's change never moves another's) rather than probing for a
403 that cannot exist.

THE DIGEST
----------
"digest batches N into 1 and marks them; IMMEDIATE unaffected." Marking matters
as much as batching: a batched notification that stayed QUEUED and deferred
would be chased by the escalation ladder for mail that has already gone out.
"""
from __future__ import annotations

import json

import pytest

from utils import notifications, notifications_store as ns, supplier_accounts
from utils.procurement_agent.tests._arc4_notifications_fixtures import (  # noqa: F401
    APP_ORIGIN, active_member, allowlist, install_fake_provider,
    isolate_notification_stores, login, notif_api, open_rfq,
)

PREFS = "/api/supplier/notification-preferences"


def put_pref(client, value: str):
    """PUT the preference with the ``Origin`` arc 3's CSRF check requires on a
    cookie-authenticated state-changing request (D2). A bearer would be exempt;
    a browser cookie is ambient and is not."""
    return client.put(PREFS, json={"preference": value},
                      headers={"Origin": APP_ORIGIN})


@pytest.fixture
def prefs_api(notif_api, monkeypatch):
    """A logged-in OWNER of dxpe.com, with the real governance gate open."""
    monkeypatch.setenv("SEND_GOVERNANCE_V1", "1")
    provider = install_fake_provider(monkeypatch)
    allowlist("dxpe.com")
    acct, member = active_member(notif_api)
    login(notif_api, monkeypatch)
    # The sign-in link itself went through the fake transport; clear it so each
    # test's outbox assertions are about the notification under test.
    provider.outbox.clear()
    notif_api._provider = provider
    return notif_api, acct, member


# ---------------------------------------------------------------------------
# The preferences API
# ---------------------------------------------------------------------------

def test_the_default_is_immediate(prefs_api):
    client, _, _ = prefs_api
    r = client.get(PREFS)
    assert r.status_code == 200, r.text
    assert r.json() == {"preference": "IMMEDIATE",
                        "choices": ["IMMEDIATE", "DAILY_DIGEST", "NONE"]}


@pytest.mark.parametrize("wanted", ["IMMEDIATE", "DAILY_DIGEST", "NONE"])
def test_every_preference_round_trips(prefs_api, wanted):
    client, _, member = prefs_api
    r = put_pref(client, wanted)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "preference": wanted}
    assert client.get(PREFS).json()["preference"] == wanted
    assert ns.get_preference(member["id"]) == wanted


def test_a_lowercase_preference_is_accepted(prefs_api):
    """The stored vocabulary is upper-case; a client sending "immediate" means
    IMMEDIATE, and rejecting it would be pedantry, not validation."""
    client, _, _ = prefs_api
    assert put_pref(client, "daily_digest").json() == {
        "ok": True, "preference": "DAILY_DIGEST"}


def test_an_unknown_preference_is_a_422_not_a_silent_noop(prefs_api):
    """A supplier who believes they turned notifications down, and did not, is
    exactly the complaint this surface exists to prevent."""
    client, _, member = prefs_api
    r = put_pref(client, "WEEKLY")
    assert r.status_code == 422
    assert "IMMEDIATE" in r.json()["detail"]
    assert ns.get_preference(member["id"]) == "IMMEDIATE"
    assert put_pref(client, "").status_code == 422


def test_one_members_preference_never_moves_anothers(prefs_api):
    client, acct, owner = prefs_api
    colleague = supplier_accounts.add_member(
        acct["id"], "buyer@dxpe.com", role=supplier_accounts.ROLE_MEMBER,
        status=supplier_accounts.MEMBER_ACTIVE)
    put_pref(client, "NONE")
    assert ns.get_preference(owner["id"]) == "NONE"
    assert ns.get_preference(colleague["id"]) == "IMMEDIATE"


def test_the_preference_change_is_audited(prefs_api):
    client, acct, member = prefs_api
    put_pref(client, "NONE")
    events = [row["event"] for row in
              supplier_accounts.list_audit(account_id=acct["id"])]
    assert "notification_preference_set" in events


def test_an_unauthenticated_caller_gets_the_uniform_401(notif_api):
    assert notif_api.get(PREFS).status_code == 401
    assert notif_api.put(PREFS, json={"preference": "NONE"},
                         headers={"Origin": APP_ORIGIN}).status_code == 401


def test_flag_off_the_routes_do_not_exist(prefs_api, monkeypatch):
    client, _, _ = prefs_api
    monkeypatch.setenv("NOTIFICATIONS_V1", "")
    for r in (client.get(PREFS), put_pref(client, "NONE")):
        assert r.status_code == 404
        assert r.json() == {"detail": "Not Found"}


# ---------------------------------------------------------------------------
# The preference's effect on the fan-out (D7)
# ---------------------------------------------------------------------------

def fan_out(client, acct, *, run_id="run-1") -> list:
    sm_id = open_rfq(domain="dxpe.com", run_id=run_id)
    return notifications.notify_rfq_new(
        {"run_id": run_id, "supplier_domain": "dxpe.com",
         "sent_message_id": sm_id, "manufacturer": "Gusher Pumps",
         "part_number": "84004-28", "quantity": 2}, acct)


def test_immediate_sends_now(prefs_api):
    client, acct, _ = prefs_api
    (n,) = fan_out(client, acct)
    assert n["state"] == ns.STATE_SENT
    assert not n["deferred"]
    assert len(client._provider.outbox) == 1


def test_daily_digest_defers_rather_than_sending(prefs_api):
    client, acct, _ = prefs_api
    put_pref(client, "DAILY_DIGEST")
    (n,) = fan_out(client, acct)
    assert n["state"] == ns.STATE_QUEUED
    assert n["deferred"] == 1
    assert client._provider.outbox == [], "a digest member was mailed immediately"


def test_none_gets_no_rfq_notification_at_all(prefs_api):
    client, acct, _ = prefs_api
    put_pref(client, "NONE")
    assert fan_out(client, acct) == []
    assert ns.list_notifications(kind=ns.KIND_RFQ_NEW) == []
    assert client._provider.outbox == []


def test_none_still_receives_auth_mail(prefs_api):
    """D7 is explicit: NONE is about RFQ mail. A member who has opted out of
    notifications must still be able to sign in."""
    client, acct, member = prefs_api
    put_pref(client, "NONE")
    client._provider.outbox.clear()
    status = supplier_accounts.send_magic_link_email(
        member["email"], "raw-token-xyz", account_domain="dxpe.com",
        member_id=member["id"])
    assert status == "sent"
    assert len(client._provider.outbox) == 1
    assert ns.list_notifications(kind=ns.KIND_AUTH_MAGIC_LINK)


# ---------------------------------------------------------------------------
# run_daily_digest
# ---------------------------------------------------------------------------

def deferred_notification(*, run_id: str, member_id: str = "m-1",
                          recipient: str = "sales@dxpe.com",
                          manufacturer: str = "Gusher Pumps") -> dict:
    n = ns.create_notification(
        kind=ns.KIND_RFQ_NEW, account_id="acct-1", member_id=member_id,
        run_id=run_id, supplier_domain="dxpe.com", recipient=recipient,
        deferred=True, detail={"manufacturer": manufacturer,
                               "part_number": f"pn-{run_id}"},
        is_test=True)
    assert n is not None
    return n


def test_the_digest_batches_n_into_one_mail_and_marks_them(prefs_api):
    client, _, _ = prefs_api
    items = [deferred_notification(run_id=f"run-{i}") for i in range(1, 4)]

    result = notifications.run_daily_digest()
    assert result == {"members": 1, "notifications": 3}
    assert len(client._provider.outbox) == 1, "one mail per member per day"

    mail = client._provider.outbox[0]
    assert mail["to"] == ["sales@dxpe.com"]
    assert "3 quote request(s)" in mail["subject"]
    for item in items:
        after = ns.get_notification(item["id"])
        assert after["deferred"] == 0, "a batched item stayed in the deferred pool"
        assert after["state"] == ns.STATE_SENT, (
            "a batched item left QUEUED would be chased by the escalation "
            "ladder for mail that has already gone out")

    (digest,) = ns.list_notifications(kind=ns.KIND_RFQ_DIGEST)
    assert sorted(digest["detail"]["batched"]) == sorted(i["id"] for i in items)


def test_the_digest_is_one_mail_per_member_not_per_account(prefs_api):
    client, _, _ = prefs_api
    deferred_notification(run_id="run-1", member_id="m-1", recipient="a@dxpe.com")
    deferred_notification(run_id="run-2", member_id="m-1", recipient="a@dxpe.com")
    deferred_notification(run_id="run-3", member_id="m-2", recipient="b@dxpe.com")
    assert notifications.run_daily_digest() == {"members": 2, "notifications": 3}
    assert sorted(m["to"][0] for m in client._provider.outbox) == \
        ["a@dxpe.com", "b@dxpe.com"]


def test_a_second_run_the_same_day_sends_nothing(prefs_api):
    client, _, _ = prefs_api
    deferred_notification(run_id="run-1")
    assert notifications.run_daily_digest()["members"] == 1
    assert notifications.run_daily_digest() == {"members": 0, "notifications": 0}
    assert len(client._provider.outbox) == 1


def test_immediate_notifications_are_untouched_by_the_digest(prefs_api):
    client, acct, _ = prefs_api
    (immediate,) = fan_out(client, acct, run_id="run-immediate")
    client._provider.outbox.clear()
    assert notifications.run_daily_digest() == {"members": 0, "notifications": 0}
    assert client._provider.outbox == []
    assert ns.get_notification(immediate["id"])["state"] == ns.STATE_SENT


def test_a_suppressed_address_gets_no_digest(prefs_api):
    client, _, _ = prefs_api
    deferred_notification(run_id="run-1")
    ns.suppress_email("sales@dxpe.com", reason="hard_bounce", is_test=True)
    assert notifications.run_daily_digest() == {"members": 0, "notifications": 0}
    assert client._provider.outbox == []


def test_the_digest_body_lists_every_batched_part(prefs_api):
    client, _, _ = prefs_api
    deferred_notification(run_id="run-1", manufacturer="Gusher Pumps")
    deferred_notification(run_id="run-2", manufacturer="SKF")
    notifications.run_daily_digest()
    body = client._provider.outbox[0]["body"]
    assert "Gusher Pumps" in body and "SKF" in body
    assert "/supplier/requests" in body


def test_a_deferred_item_is_not_escalated_while_the_digest_owns_it(prefs_api):
    """The two schedulers must not fight: an item waiting for tonight's digest
    is not an unanswered RFQ yet."""
    client, _, _ = prefs_api
    item = deferred_notification(run_id="run-1")
    from datetime import datetime, timedelta, timezone
    late = datetime.now(timezone.utc) + timedelta(hours=48)
    assert notifications.run_escalations(late) == {"reminded": 0, "alerted": 0,
                                                   "considered": 1}
    assert ns.get_notification(item["id"])["reminded_at"] is None


def test_the_flag_off_digest_is_a_noop(prefs_api, monkeypatch):
    client, _, _ = prefs_api
    deferred_notification(run_id="run-1")
    monkeypatch.setenv("NOTIFICATIONS_V1", "")
    assert notifications.run_daily_digest() == {"members": 0, "notifications": 0}
    assert client._provider.outbox == []


def test_the_cli_runs_the_digest(prefs_api, capsys):
    from scripts import notifications_scheduler as cli
    client, _, _ = prefs_api
    deferred_notification(run_id="run-1")
    deferred_notification(run_id="run-2")
    assert cli.main(["digest", "--json"]) == 0
    # The store and the send seam log as they go; the JSON line is the last one.
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload == {"command": "digest", "members": 1, "notifications": 2}
    assert len(client._provider.outbox) == 1
