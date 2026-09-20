"""
utils/notifications.py
Arc 4 — the notification SERVICE: the layer between the product's send seams
and ``utils/notifications_store.py``.

WHAT LIVES HERE (and what deliberately does not)
------------------------------------------------
Here: the ``NOTIFICATIONS_V1`` gate, the RFQ_NEW fan-out (T4), auth-mail
ledgering (T3/D9), delivery-event application (T6/T9), the escalation ladder
(T8) and the daily digest (T10).

Not here: persistence (the store), transport (``utils/mail_provider``), SNS
envelope verification (``utils/ses_webhook``). This module never opens a
socket and never touches sqlite directly.

THE Q1 RULING, AND WHY IT SHAPES THIS FILE
-------------------------------------------
``RFQ_NEW`` is owned by the ``rfq_send`` seam — the moment an RFQ is actually
sent and the ``sent_messages`` row is written — NOT by ``tier1_notify``. That
row is what the portal inbox renders, so it is the only subject a supplier can
subsequently *view*, which is precisely what D5's "seen" signal requires.
``tier1_notify`` writes no such row, so a notification raised there would have
nothing to view and would escalate 100% of the time: a ladder that always
fires is noise, not signal. ``TIER1_FYI`` exists as a trackable kind for that
path, and ``ESCALATABLE_KINDS`` excludes it.

FLAG POSTURE
------------
``notifications_active()`` is read LIVE on every entry point. With the flag
off every public function here is a no-op that returns ``None`` / ``[]`` /
``0`` — no rows written, no mail built, no store touched. That is the whole
of the "flags off = today's behaviour exactly" guarantee for this module.

FAIL-SOFT
---------
Every entry point is wrapped: a notification failure must never break the send
it is describing. An RFQ that went out but whose notification row failed to
write is a tracking gap; an RFQ that failed to go out because tracking threw
is a product outage. The asymmetry is deliberate.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from utils import notifications_store as store
from utils.mail_provider import notifications_active  # re-exported: ONE flag reader

# ---------------------------------------------------------------------------
# Config (G9 convention: named env constants, typed reader, safe defaults)
# ---------------------------------------------------------------------------

ENV_REMIND_HOURS = "ESCALATE_REMIND_HOURS"
ENV_ALERT_HOURS = "ESCALATE_ALERT_HOURS"
DEFAULT_REMIND_HOURS = 4.0
DEFAULT_ALERT_HOURS = 24.0

# D8: soft bounces do not suppress; this many CONSECUTIVE ones raise an alert.
SOFT_BOUNCE_ALERT_THRESHOLD = 3

# SES event type -> the state it maps to (D4's table, verbatim).
SES_EVENT_STATES: dict[str, str] = {
    "Send": store.STATE_SENT,
    "Delivery": store.STATE_DELIVERED,
    "Open": store.STATE_OPENED,
    "Click": store.STATE_CLICKED,
    "Bounce": store.STATE_BOUNCED,
    "Complaint": store.STATE_COMPLAINED,
    "Reject": store.STATE_REJECTED,
}


def _env_float(name: str, default: float) -> float:
    """Read an hour threshold. Unset or unparseable ⇒ the default — a typo in
    a schedule must not silently disable escalation (or fire it instantly)."""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"[Notifications] {name}={raw!r} is not a number — using {default}")
        return default


def remind_hours() -> float:
    """D6's reminder threshold (default 4, wall-clock)."""
    return _env_float(ENV_REMIND_HOURS, DEFAULT_REMIND_HOURS)


def alert_hours() -> float:
    """D6's concierge-alert threshold (default 24, wall-clock)."""
    return _env_float(ENV_ALERT_HOURS, DEFAULT_ALERT_HOURS)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: Optional[str]) -> Optional[datetime]:
    """Tolerant ISO parse → aware UTC (naive treated as UTC), ``None`` on
    blank/unparseable — the same contract as ``supplier_accounts._parse_dt``."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# T3 / D9 — auth mail joins the governance ledger
# ---------------------------------------------------------------------------

def record_auth_send(*, supplier_domain: Optional[str], recipient: str,
                     subject: str) -> Optional[str]:
    """Write the pre-attempt ``sent_messages`` row for an auth mail (D9).

    THE BODY IS NOT RECORDED. A magic-link body contains the raw token; the
    store holds only its digest, and the ledger must not become the one place
    a live credential is written down. Subject, recipient, domain and class are
    enough for cap accounting, the digest and the audit trail — which is what
    D9 actually asks for.

    Returns the row id, or ``None`` (flag off / store failure).
    """
    if not notifications_active():
        return None
    try:
        from utils import supplier_registry
        return supplier_registry.record_sent_message(
            run_id=None, supplier_domain=supplier_domain, vendor_name=None,
            to=[recipient], cc=[], subject=subject, body=None,
            status="released", message_class=supplier_registry.MESSAGE_CLASS_AUTH,
            with_history=True)
    except Exception as exc:
        print(f"[Notifications] record_auth_send failed: {exc}")
        return None


def track_auth_notification(*, kind: str, recipient: str,
                            supplier_domain: Optional[str],
                            member_id: Optional[str], status: str,
                            provider_message_id: Optional[str] = None
                            ) -> Optional[dict]:
    """Record an auth-class send as a tracked ``Notification`` (D3/D5).

    Auth mail is tracked for DELIVERY, not for escalation: a magic link or an
    invite has no RFQ to be unseen, so neither kind is in
    ``ESCALATABLE_KINDS``. What tracking buys is D8 — a hard bounce on a
    sign-in link suppresses that address and alerts a human, instead of the
    person quietly never being able to log in.

    Fail-soft ``None``; never raises into an auth flow.
    """
    if not notifications_active():
        return None
    try:
        from utils.mail_provider import auth_configuration_set
        notification = store.create_notification(
            kind=kind, member_id=member_id, supplier_domain=supplier_domain,
            recipient=recipient, configuration_set=auth_configuration_set())
        if notification is None:
            return None
        if provider_message_id:
            store.set_provider_message_id(notification["id"], provider_message_id)
        if status == "sent":
            return store.transition(notification["id"], store.STATE_SENT,
                                    event_type="Send")
        if status in ("suppressed", "not_allowlisted", "cap_blocked"):
            return store.transition(notification["id"], store.STATE_SUPPRESSED,
                                    event_type=status)
        if status == "error":
            return store.transition(notification["id"], store.STATE_FAILED,
                                    event_type="Error")
        return notification        # "stubbed" — nothing left, stays QUEUED
    except Exception as exc:
        print(f"[Notifications] track_auth_notification failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# T4 — the RFQ_NEW fan-out, at the rfq_send seam (Q1 RULED)
# ---------------------------------------------------------------------------

def notifiable_members(account_id: str) -> list[dict]:
    """The members of an account who may be notified at all (D7).

    Three filters, in order, each for a different reason:
      - ACTIVE membership (a pending or revoked person is not a recipient);
      - the ``VIEW_REQUESTS`` capability, via the arc 2/3 RBAC matrix — a
        member who cannot see requests must not be told about one;
      - not address-suppressed (D8) — a hard-bounced or complained address is
        excluded from every later fan-out, which is the whole point of
        suppressing it.

    Preference is NOT applied here: ``NONE`` members still receive auth and
    invite mail (D7), so the preference split belongs at the RFQ_NEW call site,
    not in the notion of "notifiable".
    """
    from utils import supplier_accounts
    from utils.supplier_accounts_rbac import VIEW_REQUESTS, has_permission
    out: list[dict] = []
    for m in supplier_accounts.list_members(account_id):
        if m.get("status") != supplier_accounts.MEMBER_ACTIVE:
            continue
        if not has_permission(m, VIEW_REQUESTS):
            continue
        if store.is_email_suppressed(m.get("email") or ""):
            continue
        out.append(m)
    return out


def _rfq_subject_and_body(rfq: dict) -> tuple[str, str]:
    """The RFQ_NEW mail. Deliberately content-free about price and buyer: it
    says a request is waiting and points at the portal, because the portal is
    where the supplier is meant to act (and where viewing it produces D5's
    strong 'seen' signal)."""
    part = " ".join(str(x) for x in (rfq.get("manufacturer"), rfq.get("part_number")) if x)
    subject = f"New quote request{f' — {part}' if part else ''}"
    from utils.supplier_accounts import magic_link_url
    portal = magic_link_url("").split("/supplier/verify")[0] + "/supplier/requests"
    quantity = rfq.get("quantity")
    body = (
        "Hello,\n\n"
        f"Arkim has sent you a request for quote{f' for {part}' if part else ''}.\n"
        f"{f'Quantity: {quantity}' + chr(10) if quantity else ''}"
        "\nYou can review it and submit a quote in your supplier portal:\n"
        f"{portal}\n\n"
        "Regards,\nArkim Procurement\nprocurement@arkim.ai"
    )
    return subject, body


def _send_notification_mail(notification: dict, *, subject: str, body: str,
                            recipient: str) -> dict:
    """Hand one notification's mail to THE send seam and record the outcome.

    ``GmailSender().send`` is used because it is the seam — governance runs
    INSIDE it (suppression → allowlist → caps) ahead of the delivery gate and
    the transport. Calling a provider directly from here is exactly the D1
    bypass this arc exists to prevent.

    State mapping is honest about what actually happened:
      ``sent``             → SENT, with the provider id bound for the webhook;
      governance-blocked   → SUPPRESSED (the real verdict, not a fake success);
      ``stubbed``          → left QUEUED (the delivery gate is off; nothing went
                             out, and claiming SENT would make the ladder judge
                             a message that does not exist);
      ``error``            → FAILED.
    """
    from utils.email_sender import EmailMessage, GmailSender
    msg = EmailMessage(
        to=[recipient], subject=subject, body=body,
        metadata={
            "supplier_domain": notification.get("supplier_domain"),
            "notification_id": notification["id"],
            "notification_kind": notification["kind"],
            "run_id": notification.get("run_id"),
        },
    )
    result = GmailSender().send(msg)
    if result.status == "sent":
        if result.message_id:
            store.set_provider_message_id(notification["id"], result.message_id)
        return store.transition(notification["id"], store.STATE_SENT,
                                event_type="Send") or notification
    if result.status in ("suppressed", "not_allowlisted", "cap_blocked"):
        return store.transition(notification["id"], store.STATE_SUPPRESSED,
                                event_type=result.status,
                                detail={"reason": result.error}) or notification
    if result.status == "error":
        return store.transition(notification["id"], store.STATE_FAILED,
                                event_type="Error",
                                detail={"reason": result.error}) or notification
    # "stubbed": the delivery gate is off. Stay QUEUED — truthfully.
    return notification


def notify_rfq_new(rfq: dict, account: Optional[dict]) -> list[dict]:
    """Fan one sent RFQ out to an account's notifiable members (T4 / D7).

    ``rfq`` carries ``run_id``, ``supplier_domain``, ``sent_message_id`` (the
    ledger row the portal inbox renders — the notification's subject ref) and
    optionally ``manufacturer`` / ``part_number`` / ``quantity``.

    Per-member behaviour by preference (D7):
      IMMEDIATE    — one notification, QUEUED → SENT now;
      DAILY_DIGEST — one notification, QUEUED and ``deferred``; the digest
                     scheduler sends it later;
      NONE         — no notification at all for RFQ mail.

    An account with no notifiable members (including no account at all) is
    itself a concierge alert — the supplier cannot be told, and a human needs
    to know that rather than the request silently evaporating.

    Returns the notifications created (``[]`` when the flag is off).
    """
    if not notifications_active():
        return []
    try:
        return _notify_rfq_new(rfq, account)
    except Exception as exc:
        print(f"[Notifications] notify_rfq_new failed: {exc}")
        return []


def _notify_rfq_new(rfq: dict, account: Optional[dict]) -> list[dict]:
    domain = rfq.get("supplier_domain") or ""
    run_id = rfq.get("run_id")
    account_id = (account or {}).get("id")
    members = notifiable_members(account_id) if account_id else []
    if not members:
        store.raise_alert(
            kind=store.ALERT_NO_NOTIFIABLE_MEMBERS,
            dedupe_key=f"no-members:{run_id}:{domain}",
            account_id=account_id, run_id=run_id, supplier_domain=domain,
            detail={"reason": "no account" if not account_id
                    else "no ACTIVE member holds view_requests, or all are suppressed"})
        print(f"[Notifications] RFQ_NEW for {domain}: no notifiable members -> alert")
        return []

    subject, body = _rfq_subject_and_body(rfq)
    from utils.mail_provider import notifications_configuration_set
    created: list[dict] = []
    for member in members:
        pref = store.get_preference(member["id"])
        if pref == store.PREF_NONE:
            continue
        notification = store.create_notification(
            kind=store.KIND_RFQ_NEW, account_id=account_id,
            member_id=member["id"], subject_ref=rfq.get("sent_message_id"),
            run_id=run_id, supplier_domain=domain, recipient=member["email"],
            configuration_set=notifications_configuration_set(),
            deferred=(pref == store.PREF_DAILY_DIGEST),
            detail={"manufacturer": rfq.get("manufacturer"),
                    "part_number": rfq.get("part_number"),
                    "quantity": rfq.get("quantity")})
        if notification is None:
            continue
        if pref == store.PREF_IMMEDIATE:
            notification = _send_notification_mail(
                notification, subject=subject, body=body,
                recipient=member["email"])
        created.append(notification)
    return created


def notify_rfq_sent(*, sent_message_id: Optional[str], run_id: Optional[str],
                    supplier_domain: Optional[str], status: str) -> list[dict]:
    """The ``rfq_send`` hook (Q1 RULED). Fail-soft by construction.

    Fires only for a send that produced something the supplier can actually
    VIEW — a ledger row in ``OPEN_RFQ_STATUSES``, which is exactly what
    ``_supplier_open_requests`` renders in the portal inbox. A blocked or
    errored send has no inbox row, so notifying about it would create a
    notification that must escalate; the send's own failure handling already
    covers that case honestly.
    """
    if not notifications_active():
        return []
    try:
        from utils import supplier_accounts, supplier_registry
        if status not in supplier_registry.OPEN_RFQ_STATUSES:
            return []
        if not supplier_domain or not sent_message_id:
            return []
        account = supplier_accounts.get_account_by_domain(supplier_domain)
        rfq: dict[str, Any] = {"run_id": run_id, "supplier_domain": supplier_domain,
                               "sent_message_id": sent_message_id}
        return notify_rfq_new(rfq, account)
    except Exception as exc:
        print(f"[Notifications] notify_rfq_sent failed: {exc}")
        return []


def notify_tier1_fyi(*, supplier_domain: str, run_id: Optional[str],
                     recipient: Optional[str] = None) -> Optional[dict]:
    """Track a Tier-1 FYI as a notification WITHOUT entering the D6 ladder.

    T4's optional clause. The FYI writes no ``sent_messages`` row, so there is
    nothing for the supplier to view and nothing for ``RfqView`` to record —
    which is why its kind is excluded from ``ESCALATABLE_KINDS`` rather than
    being an RFQ_NEW with an exception bolted on.
    """
    if not notifications_active():
        return None
    try:
        return store.create_notification(
            kind=store.KIND_TIER1_FYI, run_id=run_id,
            supplier_domain=supplier_domain, recipient=recipient)
    except Exception as exc:
        print(f"[Notifications] notify_tier1_fyi failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# T7 — portal read state (D5)
# ---------------------------------------------------------------------------

def record_view(*, run_id: str, supplier_domain: str,
                member_id: Optional[str] = None,
                sent_message_id: Optional[str] = None) -> None:
    """Record that a credential viewed an RFQ in the portal. Fail-soft and
    silent: a failure to record a view must never fail the inbox read that
    the supplier is actually waiting on."""
    if not notifications_active():
        return
    try:
        store.record_rfq_view(run_id=run_id, supplier_domain=supplier_domain,
                              member_id=member_id, sent_message_id=sent_message_id)
    except Exception as exc:
        print(f"[Notifications] record_view failed for {run_id!r}: {exc}")


def seen_run_ids(supplier_domain: str) -> set[str]:
    """Runs this supplier has already viewed — read BEFORE the current render
    is recorded, so T11's indicator can show "new" on the first view."""
    if not notifications_active():
        return set()
    try:
        return store.viewed_run_ids(supplier_domain)
    except Exception as exc:
        print(f"[Notifications] seen_run_ids failed: {exc}")
        return set()


# ---------------------------------------------------------------------------
# T6 / T9 — applying a provider delivery event
# ---------------------------------------------------------------------------

def apply_delivery_event(event: dict) -> bool:
    """Apply one normalised SES event to its notification (D3/D4/D8).

    ``event`` is ``{"event_type", "provider_message_id", "recipients",
    "bounce_type", "bounce_subtype", "raw"}`` as produced by
    ``ses_webhook.parse_ses_event``.

    Idempotency is claimed FIRST, by an INSERT that the unique index arbitrates
    — so a redelivered SNS event is a no-op even if two workers race on it.
    Returns ``True`` when the event was newly applied.
    """
    if not notifications_active():
        return False
    try:
        return _apply_delivery_event(event)
    except Exception as exc:
        print(f"[Notifications] apply_delivery_event failed: {exc}")
        return False


def _apply_delivery_event(event: dict) -> bool:
    event_type = event.get("event_type") or ""
    pmid = event.get("provider_message_id") or ""
    to_state = SES_EVENT_STATES.get(event_type)
    if not pmid or to_state is None:
        return False
    if not store.claim_provider_event(pmid, event_type):
        return False                       # replay — no-op (D4)

    notification = store.get_notification_by_provider_message_id(pmid)
    if notification is not None:
        store.transition(notification["id"], to_state, event_type=event_type,
                         detail={"bounce_type": event.get("bounce_type"),
                                 "bounce_subtype": event.get("bounce_subtype")})
    _apply_suppression_side_effects(event, event_type, notification)
    return True


def _apply_suppression_side_effects(event: dict, event_type: str,
                                    notification: Optional[dict]) -> None:
    """D8: what a bounce or complaint does BEYOND the state change.

    Hard bounce or complaint ⇒ suppress that ADDRESS (never the domain — gate
    FINDING F3) and alert a human. Soft bounces do not suppress; three
    consecutive ones alert. A delivery resets the soft counter, which is what
    makes "consecutive" mean consecutive.
    """
    recipients = [r for r in (event.get("recipients") or []) if r]
    if not recipients and notification and notification.get("recipient"):
        recipients = [notification["recipient"]]
    account_id = (notification or {}).get("account_id")
    member_id = (notification or {}).get("member_id")
    notification_id = (notification or {}).get("id")

    if event_type == "Delivery":
        for addr in recipients:
            store.reset_soft_bounce(addr)
        return

    if event_type == "Complaint":
        for addr in recipients:
            store.suppress_email(addr, reason="complaint", member_id=member_id,
                                 account_id=account_id, detail=event.get("raw"))
            store.raise_alert(kind=store.ALERT_EMAIL_SUPPRESSED,
                              dedupe_key=f"suppressed:complaint:{addr}",
                              account_id=account_id, member_id=member_id,
                              notification_id=notification_id, email=addr,
                              detail={"reason": "complaint"})
        return

    if event_type == "Bounce":
        hard = (event.get("bounce_type") or "").lower() == "permanent"
        for addr in recipients:
            if hard:
                store.suppress_email(addr, reason="hard_bounce",
                                     member_id=member_id, account_id=account_id,
                                     detail=event.get("raw"))
                store.raise_alert(
                    kind=store.ALERT_EMAIL_SUPPRESSED,
                    dedupe_key=f"suppressed:hard_bounce:{addr}",
                    account_id=account_id, member_id=member_id,
                    notification_id=notification_id, email=addr,
                    detail={"reason": "hard_bounce",
                            "subtype": event.get("bounce_subtype")})
            else:
                count = store.bump_soft_bounce(addr)
                if count >= SOFT_BOUNCE_ALERT_THRESHOLD:
                    store.raise_alert(
                        kind=store.ALERT_SOFT_BOUNCE_REPEATED,
                        dedupe_key=f"soft_bounce:{addr}:{count}",
                        account_id=account_id, member_id=member_id,
                        notification_id=notification_id, email=addr,
                        detail={"consecutive": count})


# ---------------------------------------------------------------------------
# T8 — the escalation ladder (D5/D6)
# ---------------------------------------------------------------------------

def is_seen(notification: dict) -> bool:
    """D5's "seen", and the whole reason this function exists separately.

    Seen means the supplier VIEWED the request in the portal, or CLICKED the
    link in the mail. An OPEN never counts: Apple Mail Privacy Protection
    fires the tracking pixel for every recipient whether or not a human looked
    at anything, so treating an open as seen would silently switch escalation
    off for a large share of real suppliers — the exact failure this product
    exists to prevent.
    """
    if notification.get("clicked_at"):
        return True
    run_id = notification.get("run_id")
    if not run_id:
        return False
    return store.rfq_viewed(run_id, member_id=notification.get("member_id"),
                            supplier_domain=notification.get("supplier_domain"))


def decide_escalation(notification: dict, *, now: datetime,
                      remind_after: float, alert_after: float) -> Optional[str]:
    """Pure decision function: ``"remind"``, ``"alert"``, or ``None``.

    Separated from the doing so the ladder is table-testable with no store and
    no clock. Age is measured from the send (``sent_at``), falling back to
    creation — a notification still QUEUED because the delivery gate is shut
    has never reached anyone, so its age is measured from when the system
    committed to telling them.

    Order matters: the alert threshold is checked FIRST. A notification the
    scheduler has not looked at for 30 hours should go straight to a human,
    not collect a reminder now and an alert on the next run.
    """
    if notification.get("kind") not in store.ESCALATABLE_KINDS:
        return None
    if notification.get("state") in store.TERMINAL_STATES:
        return None                       # bounced/complained: mail is not the channel
    if notification.get("deferred"):
        return None                       # the digest owns it until it goes out
    if notification.get("escalated_at"):
        # The ladder's last rung has been climbed: a human owns this request
        # now, and D6 is explicit that escalation sends NO further email to the
        # supplier. Without this the reminder branch below would fire on the
        # next run for a notification whose first scheduler pass came in late
        # (past the alert threshold, so it alerted without ever reminding) —
        # a reminder arriving after the hand-off to a human, which is exactly
        # the wrong order.
        return None
    if is_seen(notification):
        return None
    reference = _parse(notification.get("sent_at")) or _parse(notification.get("created_at"))
    if reference is None:
        return None
    age_hours = (now - reference).total_seconds() / 3600.0
    if age_hours >= alert_after:
        return "alert"
    if age_hours >= remind_after and not notification.get("reminded_at"):
        return "remind"
    return None


def run_escalations(now: Optional[datetime] = None) -> dict:
    """The D6 scheduler entry point. Callable from cron / an ECS scheduled
    task — a plain function, NOT a timer: no thread, no APScheduler, nothing
    in-process that would fire without an operator scheduling it.

    Idempotent: ``mark_reminded`` / ``mark_escalated`` are write-guarded on a
    NULL column, and the alert is deduplicated on the notification id, so a
    second run with the same ``now`` does nothing.

    Returns ``{"reminded": n, "alerted": n, "considered": n}``.
    """
    out = {"reminded": 0, "alerted": 0, "considered": 0}
    if not notifications_active():
        return out
    try:
        moment = now or _now()
        remind_after, alert_after = remind_hours(), alert_hours()
        for notification in store.list_notifications(kind=store.KIND_RFQ_NEW):
            out["considered"] += 1
            action = decide_escalation(notification, now=moment,
                                       remind_after=remind_after,
                                       alert_after=alert_after)
            if action == "remind" and _send_reminder(notification):
                out["reminded"] += 1
            elif action == "alert" and _raise_escalation(notification):
                out["alerted"] += 1
    except Exception as exc:
        print(f"[Notifications] run_escalations failed: {exc}")
    return out


def _send_reminder(parent: dict) -> bool:
    """One reminder, to the same member, about the same RFQ (D6).

    The ``mark_reminded`` guard is claimed BEFORE the mail is built: if two
    schedulers overlap, the loser sends nothing. "At most one reminder per RFQ
    per member" is enforced by a write, not by a check-then-act.
    """
    if not store.mark_reminded(parent["id"]):
        return False
    reminder = store.create_notification(
        kind=store.KIND_RFQ_REMINDER, account_id=parent.get("account_id"),
        member_id=parent.get("member_id"), subject_ref=parent.get("subject_ref"),
        run_id=parent.get("run_id"), supplier_domain=parent.get("supplier_domain"),
        recipient=parent.get("recipient"),
        configuration_set=parent.get("configuration_set"),
        detail={"reminder_for": parent["id"]})
    if reminder is None:
        return False
    detail = parent.get("detail") or {}
    part = " ".join(str(x) for x in (detail.get("manufacturer"),
                                     detail.get("part_number")) if x)
    if parent.get("recipient"):
        _send_notification_mail(
            reminder,
            subject=f"Reminder: quote request waiting{f' — {part}' if part else ''}",
            body=("Hello,\n\n"
                  "A quote request from Arkim is still waiting for you in your "
                  "supplier portal. If it is not something you can quote, you can "
                  "simply reply and let us know.\n\n"
                  "Regards,\nArkim Procurement\nprocurement@arkim.ai"),
            recipient=parent["recipient"])
    return True


def _raise_escalation(parent: dict) -> bool:
    """The 24h end of the ladder: a concierge alert, and NO further email to
    the supplier (D6). Two unanswered mails is the point at which a human
    should take over, not the point at which to send a third."""
    if not store.mark_escalated(parent["id"]):
        return False
    store.raise_alert(
        kind=store.ALERT_RFQ_ESCALATION, dedupe_key=f"escalation:{parent['id']}",
        account_id=parent.get("account_id"), member_id=parent.get("member_id"),
        notification_id=parent["id"], run_id=parent.get("run_id"),
        supplier_domain=parent.get("supplier_domain"),
        email=parent.get("recipient"),
        detail={"state": parent.get("state"), "reminded_at": parent.get("reminded_at")})
    store.create_notification(
        kind=store.KIND_RFQ_ESCALATION, account_id=parent.get("account_id"),
        member_id=parent.get("member_id"), run_id=parent.get("run_id"),
        supplier_domain=parent.get("supplier_domain"),
        detail={"escalation_for": parent["id"]})
    return True


# ---------------------------------------------------------------------------
# T10 — the daily digest (D7)
# ---------------------------------------------------------------------------

def run_daily_digest(now: Optional[datetime] = None) -> dict:
    """Batch every deferred RFQ_NEW into ONE mail per member (D7/T10).

    The same scheduler entry-point shape as ``run_escalations`` — a plain
    function an operator's cron calls. Returns
    ``{"members": n, "notifications": n}``.
    """
    out = {"members": 0, "notifications": 0}
    if not notifications_active():
        return out
    try:
        pending = [n for n in store.list_notifications(kind=store.KIND_RFQ_NEW,
                                                       deferred=True)
                   if n.get("state") == store.STATE_QUEUED]
        by_member: dict[str, list[dict]] = {}
        for n in pending:
            by_member.setdefault(n.get("member_id") or "", []).append(n)
        for member_id, items in by_member.items():
            recipient = next((i.get("recipient") for i in items if i.get("recipient")), None)
            if not recipient or store.is_email_suppressed(recipient):
                continue
            digest = store.create_notification(
                kind=store.KIND_RFQ_DIGEST,
                account_id=items[0].get("account_id"), member_id=member_id or None,
                supplier_domain=items[0].get("supplier_domain"),
                recipient=recipient,
                configuration_set=items[0].get("configuration_set"),
                detail={"batched": [i["id"] for i in items]})
            if digest is None:
                continue
            _send_notification_mail(
                digest, recipient=recipient,
                subject=f"{len(items)} quote request(s) waiting for you",
                body=_digest_body(items))
            # The batched items leave the deferred pool and are marked SENT:
            # they HAVE now been communicated, by the digest. Leaving them
            # QUEUED would make the escalation ladder chase mail that went out.
            for item in items:
                store.clear_deferred(item["id"])
                store.transition(item["id"], store.STATE_SENT,
                                 event_type="Digest",
                                 detail={"digest_id": digest["id"]})
            out["members"] += 1
            out["notifications"] += len(items)
    except Exception as exc:
        print(f"[Notifications] run_daily_digest failed: {exc}")
    return out


def _digest_body(items: list[dict]) -> str:
    lines = ["Hello,", "",
             "You have quote requests waiting in your Arkim supplier portal:", ""]
    for item in items:
        detail = item.get("detail") or {}
        part = " ".join(str(x) for x in (detail.get("manufacturer"),
                                         detail.get("part_number")) if x)
        lines.append(f"  - {part or 'Quote request'}")
    from utils.supplier_accounts import magic_link_url
    portal = magic_link_url("").split("/supplier/verify")[0] + "/supplier/requests"
    lines += ["", f"Review and quote them here:\n{portal}", "",
              "Regards,", "Arkim Procurement", "procurement@arkim.ai"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Alerts (T12) — thin pass-throughs so the API layer imports ONE module
# ---------------------------------------------------------------------------

def list_open_alerts() -> list[dict]:
    """Open concierge alerts, newest first (``[]`` when the flag is off)."""
    if not notifications_active():
        return []
    return store.list_alerts(status=store.ALERT_OPEN)


def acknowledge_alert(alert_id: str, *, acknowledged_by: str) -> Optional[dict]:
    """Acknowledge one open alert. ``None`` when unknown / already
    acknowledged / the flag is off."""
    if not notifications_active():
        return None
    return store.acknowledge_alert(alert_id, acknowledged_by=acknowledged_by)
