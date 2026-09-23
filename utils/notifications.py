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
# S3: the unit of BOTH thresholds is now BUSINESS hours in the account's own
# timezone, not wall-clock hours. The env names are unchanged so an existing
# deployment keeps its knobs; the alert default moves 24 -> 8 because one
# business day IS eight business hours, and 24 business hours would be three
# working days before anyone looked at it.
DEFAULT_REMIND_HOURS = 4.0
DEFAULT_ALERT_HOURS = 8.0

# Arc 4b S2: how long an RFQ_NEW waits so that requests arriving together
# become ONE email. Three RFQs released in the same batch are one event to the
# supplier, and three emails about them is three chances to ignore the third.
ENV_COALESCE_MINUTES = "NOTIFY_COALESCE_MINUTES"
DEFAULT_COALESCE_MINUTES = 15.0

# S5: the hard ceiling on notification emails to ONE mailbox in one business
# day. Beyond it, items roll into that member's next digest — they are
# DEFERRED, never discarded. A dropped notification would be the one failure
# this whole surface exists to prevent.
ENV_MEMBER_DAILY_CEILING = "MEMBER_DAILY_NOTIFICATION_CEILING"
DEFAULT_MEMBER_DAILY_CEILING = 5

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


def coalesce_minutes() -> float:
    """S2's coalescing window (default 15). ``<= 0`` sends immediately, which
    is the escape hatch for a deployment that wants arc-4 timing back without
    a code change."""
    return _env_float(ENV_COALESCE_MINUTES, DEFAULT_COALESCE_MINUTES)


def member_daily_ceiling() -> int:
    """S5's per-mailbox daily ceiling (default 5). ``<= 0`` switches it off,
    the house convention for an inert limiter. Unparseable ⇒ the default: a
    typo must not mean "no ceiling"."""
    raw = (os.environ.get(ENV_MEMBER_DAILY_CEILING) or "").strip()
    if not raw:
        return DEFAULT_MEMBER_DAILY_CEILING
    try:
        return int(raw)
    except ValueError:
        print(f"[Notifications] {ENV_MEMBER_DAILY_CEILING}={raw!r} is not a "
              f"number — using {DEFAULT_MEMBER_DAILY_CEILING}")
        return DEFAULT_MEMBER_DAILY_CEILING


def ceiling_reached(recipient: str, day: str) -> bool:
    """Has this mailbox had its allowance of notification emails for ``day``?

    ``day`` is the account's LOCAL business day and is a PARAMETER — the
    ceiling is a function of the scheduler's supplied instant, never of the
    wall clock. Auth mail and invites never reach this code path, which is
    exactly S5's exemption: they are requested by the recipient or their
    colleague, not pushed by us.
    """
    ceiling = member_daily_ceiling()
    if ceiling <= 0 or not recipient or not day:
        return False
    return store.count_mailed_on_day(recipient, day) >= ceiling


def remind_hours() -> float:
    """The reminder threshold, in BUSINESS hours (default 4 — S3)."""
    return _env_float(ENV_REMIND_HOURS, DEFAULT_REMIND_HOURS)


def alert_hours() -> float:
    """The concierge-alert threshold, in BUSINESS hours (default 8, i.e. one
    business day — S3)."""
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


def rfq_contacts(account_id: str) -> list[dict]:
    """The members an RFQ is actually MAILED to (arc 4b S1).

    ``notifiable_members`` answers "who may be notified at all" — ACTIVE,
    holds VIEW_REQUESTS, not address-suppressed. This narrows it to the
    account's DESIGNATED RFQ contacts: OWNER and ADMIN by default, any member
    who opts in, and nobody who opts out.

    The reason is ownership, not volume. Arc 4 mailed every member who could
    see requests, so a five-person account got five emails for one RFQ and
    each of the five could reasonably assume one of the other four had it.
    One request, one owner.
    """
    from utils.supplier_accounts import member_receives_rfq
    return [m for m in notifiable_members(account_id) if member_receives_rfq(m)]


#: The request-identity keys an RFQ_NEW mail may name. Deliberately a fixed,
#: short list: everything else on a run is internal (R8's no-leak rule).
RFQ_IDENTITY_KEYS: tuple[str, ...] = (
    "manufacturer", "part_number", "description", "quantity", "need_by",
)


def rfq_identity_for_run(run_id) -> dict:
    """The part identity for one run's RFQ mail: manufacturer, part number,
    description, quantity and the needed-by date where known.

    Fail-soft: an unreadable run yields ``{}`` and the mail degrades to its
    previous generic wording rather than failing the send (R8, F-09).
    """
    if not run_id:
        return {}
    try:
        from utils.procurement_agent.state import persistence
        specs = (persistence.get_run(run_id) or {}).get("asset_specs_json") or {}
    except Exception as exc:
        print(f"[Notifications] rfq identity read failed for {run_id}: {exc}")
        return {}
    if not isinstance(specs, dict):
        return {}
    out = {}
    for key in RFQ_IDENTITY_KEYS:
        value = specs.get(key)
        if isinstance(value, str):
            value = value.strip()
        if value not in (None, "", "N/A", "Unknown", "UNKNOWN-PN", "none", "unknown"):
            out[key] = value
    # A family-level request has a model but no part number; the supplier needs
    # to know WHICH part, and "Chesterton 155" is that answer.
    if "part_number" not in out:
        model = specs.get("model")
        if isinstance(model, str) and model.strip():
            out["part_number"] = model.strip()
    return out


def rfq_part_label(rfq: dict) -> str:
    """The supplier-facing name of the part: "<manufacturer> <part number>",
    falling back to the description when no identity resolved."""
    part = " ".join(str(x) for x in (rfq.get("manufacturer"), rfq.get("part_number")) if x)
    return part or str(rfq.get("description") or "").strip()


def _rfq_subject_and_body(rfq: dict) -> tuple[str, str]:
    """The ONE-request RFQ_NEW mail. Deliberately content-free about price and
    buyer: it says a request is waiting and points at the portal, because the
    portal is where the supplier is meant to act (and where viewing it
    produces D5's strong 'seen' signal). Takes the request identity as a plain
    dict, so the fan-out and the coalescing flush build it from the same
    keys."""
    part = rfq_part_label(rfq)
    subject = f"New quote request{f' — {part}' if part else ''}"
    portal = _portal_url()
    quantity = rfq.get("quantity")
    # R8: description and needed-by where known, still with NO prices (the
    # existing no-numbers rule) and nothing internal.
    description = str(rfq.get("description") or "").strip()
    if description and description == part:
        description = ""
    need_by = str(rfq.get("need_by") or "").strip()
    body = (
        "Hello,\n\n"
        f"Arkim has sent you a request for quote{f' for {part}' if part else ''}.\n"
        f"{f'{description}' + chr(10) if description else ''}"
        f"{f'Quantity: {quantity}' + chr(10) if quantity else ''}"
        f"{f'Needed by: {need_by}' + chr(10) if need_by else ''}"
        "\nYou can review it and submit a quote in your supplier portal:\n"
        f"{portal}\n\n"
        "Regards,\nArkim Procurement\nprocurement@arkim.ai"
    )
    return subject, body


def _send_notification_mail(notification: dict, *, subject: str, body: str,
                            recipient: str, mailed_day: Optional[str] = None,
                            at: Optional[datetime] = None) -> dict:
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
    from utils import supplier_registry
    from utils.email_sender import EmailMessage, GmailSender
    from utils.mail_provider import notifications_configuration_set
    msg = EmailMessage(
        to=[recipient], subject=subject, body=body,
        metadata={
            "supplier_domain": notification.get("supplier_domain"),
            "notification_id": notification["id"],
            "notification_kind": notification["kind"],
            "run_id": notification.get("run_id"),
            # R-F8: notification mail is its OWN cap class. Absent, this
            # defaulted to "rfq" and competed with cold outbound for the RFQ
            # budget — so an exhausted RFQ day stopped telling suppliers about
            # RFQs already in their inbox.
            "message_class": supplier_registry.MESSAGE_CLASS_NOTIFICATION,
            # Gate FINDING F9: the set the row RECORDS and the set the message
            # is SENT on were resolved independently. Stamping it here makes
            # the recorded value the authoritative one.
            "configuration_set": (notification.get("configuration_set")
                                  or notifications_configuration_set()),
        },
    )
    # R-F8: record BEFORE the attempt, exactly as auth mail and rfq_send do, so
    # notification volume is visible in the ledger and countable by the cap.
    # The subject and recipient are enough; a notification body carries no
    # credential, but it carries nothing worth storing twice either.
    row_id = _record_notification_send(notification, subject=subject,
                                       recipient=recipient)
    if mailed_day:
        # S5: stamped BEFORE the attempt, so a crash mid-send costs the member
        # one slot rather than letting a retry storm past the ceiling.
        store.mark_mailed(notification["id"], mailed_day)
    # ``at`` is the scheduler's supplied instant. It is what stamps ``sent_at``,
    # and ``sent_at`` is what the escalation ladder measures age from — so a
    # run driven by an explicit ``now`` produces a ladder that agrees with it.
    # Without this the send stamped the wall clock while the ladder judged
    # against ``now``, and the two disagreed by however far apart they were:
    # the same date-dependence class as arc 4's review finding.
    stamp = at.isoformat() if at is not None else None
    result = GmailSender().send(msg)
    if row_id:
        supplier_registry.update_sent_message_status(
            row_id, result.status, message_id=result.message_id,
            thread_id=result.thread_id)
    if result.status == "cap_blocked":
        _alert_notification_cap_blocked(notification, reason=result.error)
    if result.status == "sent":
        if result.message_id:
            store.set_provider_message_id(notification["id"], result.message_id)
        return store.transition(notification["id"], store.STATE_SENT,
                                event_type="Send", at=stamp) or notification
    if result.status in ("suppressed", "not_allowlisted", "cap_blocked"):
        return store.transition(notification["id"], store.STATE_SUPPRESSED,
                                event_type=result.status, at=stamp,
                                detail={"reason": result.error}) or notification
    if result.status == "error":
        return store.transition(notification["id"], store.STATE_FAILED,
                                event_type="Error", at=stamp,
                                detail={"reason": result.error}) or notification
    # "stubbed": the delivery gate is off. Stay QUEUED — truthfully.
    return notification


def _record_notification_send(notification: dict, *, subject: str,
                              recipient: str) -> Optional[str]:
    """The pre-attempt ``sent_messages`` row for notification mail (R-F8).

    Before this existed the notification surface wrote nothing to the ledger,
    so its volume was invisible to the governance digest and — because the cap
    counts ledger rows — uncountable by its own cap. Fail-soft ``None``: a
    ledger failure must degrade accounting, never stop the notification.
    """
    try:
        from utils import supplier_registry
        return supplier_registry.record_sent_message(
            run_id=notification.get("run_id"),
            # DELIBERATELY no supplier_domain. ``get_sent_messages(domain=...)``
            # IS the RFQ-ledger read — the portal inbox and the admin RFQ views
            # are built on it — and a notification is not an RFQ. The recipient
            # address is recorded in full, so the account is still recoverable
            # from the row; what is not recoverable is a notification row
            # masquerading as a request in somebody's inbox.
            supplier_domain=None,
            vendor_name=None, to=[recipient], cc=[], subject=subject,
            body=None, status="released",
            message_class=supplier_registry.MESSAGE_CLASS_NOTIFICATION,
            with_history=True)
    except Exception as exc:
        print(f"[Notifications] record_notification_send failed: {exc}")
        return None


def _alert_notification_cap_blocked(notification: dict,
                                    reason: Optional[str] = None,
                                    now: Optional[datetime] = None) -> None:
    """R-F8's DIGEST-tier alert: the notification cap suppressed a send.

    Deduped on the UTC day, so a day that trips the cap raises ONE alert
    however many notifications it suppresses, and the next day re-arms. It is
    informational by construction — see ``ALERT_TIERS``; a cap-block is a
    config problem, not a same-hour interruption.
    """
    day = (now or _now()).strftime("%Y-%m-%d")
    try:
        store.raise_alert(
            kind=store.ALERT_NOTIFICATION_CAP_BLOCKED,
            dedupe_key=f"notification_cap:{day}",
            account_id=notification.get("account_id"),
            member_id=notification.get("member_id"),
            notification_id=notification.get("id"),
            run_id=notification.get("run_id"),
            supplier_domain=notification.get("supplier_domain"),
            email=notification.get("recipient"),
            detail={"reason": reason, "day": day})
    except Exception as exc:
        print(f"[Notifications] cap-blocked alert failed: {exc}")


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


def _notify_rfq_new(rfq: dict, account: Optional[dict],
                    now: Optional[datetime] = None) -> list[dict]:
    domain = rfq.get("supplier_domain") or ""
    run_id = rfq.get("run_id")
    account_id = (account or {}).get("id")
    members = rfq_contacts(account_id) if account_id else []
    if not members:
        store.raise_alert(
            kind=store.ALERT_NO_NOTIFIABLE_MEMBERS,
            dedupe_key=f"no-members:{run_id}:{domain}",
            account_id=account_id, run_id=run_id, supplier_domain=domain,
            detail={"reason": "no account" if not account_id
                    else "no designated RFQ contact is active, permitted and "
                         "un-suppressed"})
        print(f"[Notifications] RFQ_NEW for {domain}: no notifiable members -> alert")
        return []

    from utils.mail_provider import notifications_configuration_set
    moment = now or _now()
    window = max(coalesce_minutes(), 0.0)
    subject, body = _rfq_subject_and_body(rfq)
    created: list[dict] = []
    for member in members:
        pref = store.get_preference(member["id"])
        if pref == store.PREF_NONE:
            continue
        deferred = pref == store.PREF_DAILY_DIGEST
        recipient = member["email"]
        # S2, and the ONE judgement call in it. The first request to an idle
        # mailbox is mailed STRAIGHT AWAY — a supplier waiting on a line-down
        # part must not be held back fifteen minutes to make a tidier email —
        # and it opens a window. Every further request inside that window
        # JOINS the open batch and is mailed once, with the others, when the
        # window closes. Ten requests released together therefore produce two
        # emails, not ten, and no request goes unnamed in any of them.
        open_until = None if deferred else _open_window_until(recipient, moment)
        joins_batch = open_until is not None
        notification = store.create_notification(
            kind=store.KIND_RFQ_NEW, account_id=account_id,
            member_id=member["id"], subject_ref=rfq.get("sent_message_id"),
            run_id=run_id, supplier_domain=domain, recipient=recipient,
            configuration_set=notifications_configuration_set(),
            deferred=deferred,
            # The digest owns a DAILY_DIGEST member's mail outright, so those
            # rows carry no window: two batchers claiming the same row is how
            # a supplier gets told about the same RFQ twice.
            coalesce_until=(None if deferred else
                            (open_until.isoformat() if joins_batch
                             else (moment + timedelta(minutes=window)).isoformat())),
            detail={"manufacturer": rfq.get("manufacturer"),
                    "part_number": rfq.get("part_number"),
                    "quantity": rfq.get("quantity")})
        if notification is None:
            continue
        if not deferred and not joins_batch:
            day = business_day_for(account_id, moment)
            if ceiling_reached(recipient, day):
                # S5: over the day's allowance. DEFERRED into the digest, not
                # dropped — the row stays QUEUED and unseen, so the supplier
                # still learns about this request, just in one daily mail
                # instead of a sixth interruption.
                store.set_deferred(notification["id"])
                notification = store.get_notification(notification["id"]) \
                    or notification
            else:
                # The anchor: mailed now, and its coalesce_until is what marks
                # the mailbox's window open for the joiners behind it. The
                # flush only picks up QUEUED rows, so it is never sent twice.
                notification = _send_notification_mail(
                    notification, subject=subject, body=body,
                    recipient=recipient, mailed_day=day, at=moment)
        created.append(notification)
    return created


def business_day_for(account_id: Optional[str], now: datetime) -> str:
    """The account's LOCAL business day for ``now`` — the unit both the daily
    ceiling and the per-account escalation aggregation count in. Pure over
    ``now``; the timezone lookup is the only I/O."""
    from utils import business_hours
    return business_hours.business_date(
        now, business_hours.account_timezone(account_id))


def _open_window_until(recipient: str, now: datetime):
    """The end of this mailbox's OPEN coalescing window, or ``None`` (S2).

    Pure over ``now`` apart from the store read — no clock is consulted — so
    the batching decision is reproducible for a supplied instant.
    """
    if not recipient:
        return None
    latest = None
    for n in store.list_notifications(kind=store.KIND_RFQ_NEW,
                                      recipient=recipient, cancelled=False):
        until = _parse(n.get("coalesce_until"))
        if until is not None and until > now and (latest is None or until > latest):
            latest = until
    return latest


# ---------------------------------------------------------------------------
# T7 / S2 — the coalescing flush
# ---------------------------------------------------------------------------

def _rfq_line(notification: dict) -> str:
    """One request, as a line in a batched mail."""
    detail = notification.get("detail") or {}
    part = rfq_part_label(detail)
    quantity = detail.get("quantity")
    suffix = f" (qty {quantity})" if quantity else ""
    return f"{part or 'Quote request'}{suffix}"


def _portal_url() -> str:
    from utils.supplier_accounts import magic_link_url
    return magic_link_url("").split("/supplier/verify")[0] + "/supplier/requests"


def _batch_subject_and_body(items: list[dict]) -> tuple[str, str]:
    """ONE mail for a batch of RFQ_NEW notifications (S2).

    A single-item batch keeps arc 4's wording exactly: the common case must
    not read like a list of one. Deliberately content-free about price and
    buyer — the mail says work is waiting and points at the portal, which is
    where the supplier acts and where viewing produces D5's "seen" signal.
    """
    if len(items) == 1:
        return _rfq_subject_and_body((items[0].get("detail") or {}))
    subject = f"{len(items)} new quote requests"
    lines = ["Hello,", "",
             f"Arkim has sent you {len(items)} requests for quote:", ""]
    lines += [f"  - {_rfq_line(i)}" for i in items]
    lines += ["",
              "You can review them and submit quotes in your supplier portal:",
              _portal_url(), "", "Regards,", "Arkim Procurement",
              "procurement@arkim.ai"]
    return subject, "\n".join(lines)


def run_coalesced_sends(now: Optional[datetime] = None) -> dict:
    """Send every RFQ_NEW whose coalescing window has closed (S2).

    The same scheduler shape as ``run_escalations``: a plain function over the
    store's current state, taking ``now`` as an argument, idempotent for a
    given ``now``, with no in-process timer. Cron runs it every few minutes.

    Grouping is by RECIPIENT ADDRESS, not member id — the address is the
    mailbox, and "one email" is a promise about a mailbox.

    Within a batch ONE notification carries the mail and the provider message
    id; the rest transition to the same state citing it. Each RFQ keeps its
    own row, so the escalation ladder still judges each request separately,
    which is what makes "this one was never looked at" answerable per request.

    Returns ``{"batches": n, "notifications": n}``.
    """
    out = {"batches": 0, "notifications": 0}
    if not notifications_active():
        return out
    try:
        moment = now or _now()
        pending = [n for n in store.list_notifications(
                       kind=store.KIND_RFQ_NEW, state=store.STATE_QUEUED,
                       deferred=False, cancelled=False)
                   if _window_closed(n, moment)]
        by_recipient: dict[str, list[dict]] = {}
        for n in pending:
            by_recipient.setdefault(n.get("recipient") or "", []).append(n)
        for recipient, items in by_recipient.items():
            if not recipient or store.is_email_suppressed(recipient):
                continue
            day = business_day_for(items[0].get("account_id"), moment)
            if ceiling_reached(recipient, day):
                # S5 again, at the batch boundary: defer the whole batch into
                # the digest rather than dropping any of it.
                for item in items:
                    store.set_deferred(item["id"])
                continue
            if not _claim_batch(items):
                continue
            subject, body = _batch_subject_and_body(items)
            carrier = items[0]
            _send_notification_mail(carrier, subject=subject, body=body,
                                    recipient=recipient, mailed_day=day,
                                    at=moment)
            carried = store.get_notification(carrier["id"]) or carrier
            reached = carried.get("state") or store.STATE_SENT
            for item in items[1:]:
                store.transition(item["id"], reached, event_type="Coalesced",
                                 at=moment.isoformat(),
                                 detail={"coalesced_into": carrier["id"]})
            out["batches"] += 1
            out["notifications"] += len(items)
    except Exception as exc:
        print(f"[Notifications] run_coalesced_sends failed: {exc}")
    return out


def _window_closed(notification: dict, now: datetime) -> bool:
    """Has this notification's coalescing window closed at ``now``?

    Pure over its arguments — no clock read — so the flush is deterministic
    for a supplied instant. A row with no window (an older row, or a zero
    window) is ready immediately.
    """
    until = _parse(notification.get("coalesce_until"))
    return until is None or now >= until


def _claim_batch(items: list[dict]) -> bool:
    """Close every window in the batch BEFORE any mail is built.

    The write is the claim, so two overlapping flushes cannot both send the
    same batch. A crash between the claim and the send loses a notification
    rather than sending it twice — the safe direction, because the row stays
    QUEUED and unseen, so the escalation ladder still chases it.
    """
    return any([store.clear_coalesce(i["id"]) for i in items])


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
        # R8 (arc 5, F-09): this caller used to pass these three keys and nothing
        # else, so _rfq_subject_and_body had no part to name and every RFQ_NEW
        # degraded to a bare "New quote request" with a portal link (observed in
        # s1_outbox_final.json, mails 5-6). The identity is on the run; read it
        # here, fail-soft, exactly as rfq_send._substitute_quote_link does.
        rfq.update(rfq_identity_for_run(run_id))
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


def _was_sole_contact(account_id: Optional[str], address: str) -> bool:
    """Was ``address`` the account's ONLY notifiable RFQ contact (S6)?

    This is the whole difference between "somebody must fix this today" and
    "mention it in tomorrow's digest": losing one of three contacts is
    housekeeping, losing the last one means the next RFQ to that supplier
    reaches nobody at all.

    Judged against the contact set as it stands BEFORE the suppression is
    written. Fail-soft ``False`` — a lookup failure must not manufacture an
    ACTION_NOW interruption.
    """
    if not account_id or not address:
        return False
    try:
        contacts = {(m.get("email") or "").strip().lower()
                    for m in rfq_contacts(account_id)}
    except Exception as exc:
        print(f"[Notifications] sole-contact check failed: {exc}")
        return False
    return contacts == {(address or "").strip().lower()}


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
            sole = _was_sole_contact(account_id, addr)
            store.suppress_email(addr, reason="complaint", member_id=member_id,
                                 account_id=account_id, detail=event.get("raw"))
            store.raise_alert(kind=store.ALERT_EMAIL_SUPPRESSED,
                              dedupe_key=f"suppressed:complaint:{addr}",
                              tier=(store.TIER_ACTION_NOW if sole
                                    else store.TIER_DIGEST),
                              account_id=account_id, member_id=member_id,
                              notification_id=notification_id, email=addr,
                              detail={"reason": "complaint", "sole_contact": sole})
        return

    if event_type == "Bounce":
        hard = (event.get("bounce_type") or "").lower() == "permanent"
        for addr in recipients:
            if hard:
                # S6: the tier depends on what this address WAS to the account.
                # Judged BEFORE the suppression lands, because afterwards the
                # address is excluded from the contact set and every bounce
                # would look like the last one.
                sole = _was_sole_contact(account_id, addr)
                store.suppress_email(addr, reason="hard_bounce",
                                     member_id=member_id, account_id=account_id,
                                     detail=event.get("raw"))
                store.raise_alert(
                    kind=store.ALERT_EMAIL_SUPPRESSED,
                    dedupe_key=f"suppressed:hard_bounce:{addr}",
                    tier=(store.TIER_ACTION_NOW if sole else store.TIER_DIGEST),
                    account_id=account_id, member_id=member_id,
                    notification_id=notification_id, email=addr,
                    detail={"reason": "hard_bounce", "sole_contact": sole,
                            "subtype": event.get("bounce_subtype")})
            else:
                # A soft bounce is a full mailbox or a temporary MTA failure:
                # it does NOT suppress (D8), because tomorrow the same address
                # works again. The counter is what turns a run of them into a
                # human-visible signal.
                count = store.bump_soft_bounce(addr)
                streak_started_at = store.soft_bounce_streak_start(addr)
                if count == SOFT_BOUNCE_ALERT_THRESHOLD:
                    # ONE alert per streak. Keyed on when the streak started,
                    # so a mailbox that soft-bounces fifty times raises one
                    # alert rather than forty-eight, and a NEW streak after a
                    # successful delivery still raises its own.
                    store.raise_alert(
                        kind=store.ALERT_SOFT_BOUNCE_REPEATED,
                        dedupe_key=f"soft_bounce:{addr}:{streak_started_at}",
                        account_id=account_id, member_id=member_id,
                        notification_id=notification_id, email=addr,
                        detail={"consecutive": count,
                                "streak_started_at": streak_started_at})


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
                      remind_after: float, alert_after: float,
                      tz_name: Optional[str] = None) -> Optional[str]:
    """Pure decision function: ``"remind"``, ``"alert"``, or ``None``.

    Separated from the doing so the ladder is table-testable with no store and
    no clock. Age is measured from the send (``sent_at``), falling back to
    creation — a notification still QUEUED because the delivery gate is shut
    has never reached anyone, so its age is measured from when the system
    committed to telling them.

    S3: the age is counted in BUSINESS hours in ``tz_name`` (the account's
    timezone; the configured default when absent). A Friday-afternoon request
    therefore does not escalate over the weekend, which under wall-clock hours
    was not an occasional false alarm but a guaranteed weekly one. ``now`` and
    the timezone are both PARAMETERS — nothing in this path reads the clock,
    so the rung a row lands on cannot depend on the day the suite runs.

    Order matters: the alert threshold is checked FIRST. A notification the
    scheduler has not looked at for a whole business day should go straight to
    a human, not collect a reminder now and an alert on the next run.
    """
    if notification.get("kind") not in store.ESCALATABLE_KINDS:
        return None
    if notification.get("state") in store.TERMINAL_STATES:
        return None                       # bounced/complained: mail is not the channel
    if notification.get("cancelled_at"):
        # S4: the request itself is resolved. Chasing a supplier's silence on
        # something the buyer no longer needs is pure noise — and the kind of
        # noise that teaches people the whole channel is not worth reading.
        return None
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
    from utils import business_hours
    age_hours = business_hours.business_hours_between(reference, now, tz_name)
    if age_hours >= alert_after:
        return "alert"
    if age_hours >= remind_after and not notification.get("reminded_at"):
        return "remind"
    return None


# ---------------------------------------------------------------------------
# T9 / S4 — stop when the request is resolved
# ---------------------------------------------------------------------------

def rfq_resolution(notification: dict) -> Optional[str]:
    """Why this notification's RFQ is resolved, or ``None`` if it is not (S4).

    THE STATES THAT ACTUALLY EXIST are checked, and no model is invented for
    the ones that do not. The gate (H9) established that this repo has no
    "awarded" state and no quote-target concept anywhere, so the three real
    signals are:

      1. the RFQ's own ``sent_messages`` row has left ``OPEN_RFQ_STATUSES`` —
         it was replied to, it bounced, or the send errored. This is the
         primary signal, because it is the same predicate the supplier portal
         uses to decide whether the request is still in their inbox: if it is
         not there, nobody can view it, and a ladder measuring "unseen" on
         something unviewable can only ever escalate;
      2. every quote token for the run is revoked or expired (the closest
         thing the repo has to "the buyer withdrew this");
      3. the run reached a terminal phase (CANCELLED / COMPLETED).

    Each lookup is independently fail-soft: a store that cannot answer leaves
    the notification un-cancelled, which keeps the supplier being chased
    rather than silently dropping a live request.
    """
    run_id = notification.get("run_id")
    subject_ref = notification.get("subject_ref")
    if not run_id:
        return None
    if subject_ref:
        try:
            from utils import supplier_registry
            row = next((r for r in supplier_registry.get_sent_messages(run_id=run_id)
                        if r.get("id") == subject_ref), None)
            if row is not None and row.get("status") not in \
                    supplier_registry.OPEN_RFQ_STATUSES:
                return f"rfq_{row.get('status')}"
        except Exception as exc:
            print(f"[Notifications] resolution ledger read failed: {exc}")
    try:
        from utils import quote_tokens
        tokens = quote_tokens.list_for_run(run_id)
        # Only conclusive when tokens EXIST and all of them are shut. An empty
        # list means QUOTE_SUBMIT_V1 is off or none were minted — the absence
        # of a quote window is not the closing of one.
        if tokens and all(t.get("revoked_at") or _expired(t.get("expires_at"))
                          for t in tokens):
            return "quote_window_closed"
    except Exception as exc:
        print(f"[Notifications] resolution token read failed: {exc}")
    try:
        from utils.procurement_agent.state import persistence
        from utils.procurement_agent.state.phases import Phase
        run = persistence.get_run(run_id)
        phase = (run or {}).get("phase")
        if phase in (Phase.CANCELLED.value, Phase.COMPLETED.value):
            return f"run_{phase}"
    except Exception:
        # Deliberately silent, and the only silent branch here. The
        # orchestrator's run store is NOT on the shipping path (CLAUDE.md §8),
        # so an unreadable or absent run is the normal case, not a fault — and
        # this runs once per notification per scheduler pass, so a log line
        # would be per-row spam that buries the ledger and token failures
        # above, which ARE worth shouting about.
        pass
    return None


def _expired(expires_at: Optional[str]) -> bool:
    """A quote token whose window has passed. Compared against the stored
    instant only — no clock read is needed because the store stamps a real
    expiry and ``quote_tokens`` owns that judgement; this is the cheap
    fallback for the metadata list, which carries no ``state``."""
    parsed = _parse(expires_at)
    return parsed is not None and parsed <= _now()


def cancel_resolved(now: Optional[datetime] = None) -> dict:
    """Cancel every pending notification whose RFQ has been resolved (S4).

    Run before the ladder decides anything, so a request closed between two
    scheduler passes is out of the next reminder and can never produce an
    escalation. Cancellation is RECORDED — ``cancelled_at`` plus a reason plus
    an audit event — never a silent state change: "we stopped chasing this,
    and here is why" has to stay answerable later.

    Returns ``{"cancelled": n}``.
    """
    out = {"cancelled": 0}
    if not notifications_active():
        return out
    try:
        moment = now or _now()
        for notification in store.list_notifications(kind=store.KIND_RFQ_NEW,
                                                     cancelled=False):
            reason = rfq_resolution(notification)
            if reason and store.mark_cancelled(notification["id"], reason=reason,
                                               at=moment.isoformat()):
                out["cancelled"] += 1
    except Exception as exc:
        print(f"[Notifications] cancel_resolved failed: {exc}")
    return out


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
        # S4 FIRST: a request resolved since the last pass must not produce a
        # reminder or an escalation on this one.
        cancel_resolved(moment)
        remind_after, alert_after = remind_hours(), alert_hours()
        due: dict[str, list[dict]] = {}
        zones: dict[str, str] = {}
        for notification in store.list_notifications(kind=store.KIND_RFQ_NEW):
            out["considered"] += 1
            action = decide_escalation(notification, now=moment,
                                       remind_after=remind_after,
                                       alert_after=alert_after,
                                       tz_name=_zone_for(notification, zones))
            if action == "remind":
                # S2: collected, not sent one at a time. The decision is still
                # per RFQ; only the MAIL is consolidated.
                due.setdefault(notification.get("recipient") or "", []) \
                   .append(notification)
            elif action == "alert" and _raise_escalation(
                    notification, day=business_day_for(
                        notification.get("account_id"), moment)):
                out["alerted"] += 1
        for recipient, parents in due.items():
            if _send_consolidated_reminder(recipient, parents, now=moment,
                                           zones=zones):
                out["reminded"] += 1
    except Exception as exc:
        print(f"[Notifications] run_escalations failed: {exc}")
    return out


def _zone_for(notification: dict, cache: dict[str, str]) -> str:
    """The timezone name for a notification's account, memoised per run so a
    hundred notifications for one account cost one store read."""
    from utils import business_hours
    key = notification.get("account_id") or ""
    if key not in cache:
        cache[key] = business_hours.account_timezone(key or None)
    return cache[key]


def reminder_day(now: datetime, tz_name: Optional[str] = None) -> str:
    """The day a reminder is counted against, as ``'YYYY-MM-DD'``.

    S3: the account's LOCAL business day, not the UTC one. A supplier in Los
    Angeles reminded at 08:05 and again at 16:00 has had two reminders in one
    working day even though those instants straddle a UTC date boundary — and
    "one reminder a day" is a promise about their day, not ours. Pure over
    ``now``; no clock read.
    """
    from utils import business_hours
    return business_hours.business_date(now, tz_name)


def _reminded_on(recipient: str, day: str) -> bool:
    """Has this mailbox already had its reminder for ``day``?

    Read off the reminder rows' stamped ``day`` rather than their
    ``created_at``, so the gate answers to the scheduler's supplied instant
    and a replay agrees with the original run.
    """
    for r in store.list_notifications(kind=store.KIND_RFQ_REMINDER,
                                      recipient=recipient):
        if ((r.get("detail") or {}).get("day")) == day:
            return True
    return False


def _reminder_subject_and_body(parents: list[dict]) -> tuple[str, str]:
    """ONE reminder covering every unseen request for this mailbox (S2).

    A single-item reminder keeps arc 4's wording exactly. Chasing somebody
    three times on one morning about three requests does not make them three
    times more likely to answer; it makes the next one easier to ignore.
    """
    portal = _portal_url()
    if len(parents) == 1:
        detail = parents[0].get("detail") or {}
        part = " ".join(str(x) for x in (detail.get("manufacturer"),
                                         detail.get("part_number")) if x)
        subject = f"Reminder: quote request waiting{f' — {part}' if part else ''}"
        body = ("Hello,\n\n"
                "A quote request from Arkim is still waiting for you in your "
                "supplier portal. If it is not something you can quote, you can "
                "simply reply and let us know.\n\n"
                f"{portal}\n\n"
                "Regards,\nArkim Procurement\nprocurement@arkim.ai")
        return subject, body
    subject = f"Reminder: {len(parents)} quote requests waiting"
    lines = ["Hello,", "",
             f"{len(parents)} quote requests from Arkim are still waiting for "
             "you in your supplier portal:", ""]
    lines += [f"  - {_rfq_line(p)}" for p in parents]
    lines += ["", "If any of them are not something you can quote, you can "
                  "simply reply and let us know.", "", portal, "",
              "Regards,", "Arkim Procurement", "procurement@arkim.ai"]
    return subject, "\n".join(lines)


def _send_consolidated_reminder(recipient: str, parents: list[dict], *,
                                now: datetime,
                                zones: Optional[dict] = None) -> bool:
    """One reminder per mailbox per day, listing every unseen RFQ (S2).

    TWO GUARANTEES, BOTH WRITE-ENFORCED:

    *An RFQ never appears in two reminders.* ``mark_reminded`` is claimed on
    every parent BEFORE the mail is built, and its ``reminded_at IS NULL``
    predicate is the guard — so two overlapping schedulers cannot both claim a
    request, and a request already covered is silently excluded from the next
    batch rather than repeated.

    *At most one reminder a day for a mailbox.* A second batch becoming due
    later the same day is DEFERRED, never dropped: its parents keep
    ``reminded_at`` NULL, so they are picked up by the next day's reminder.
    Dropping them would be exactly the failure this arc exists to prevent.

    Grouped by ADDRESS rather than member id because the address is the
    mailbox, and "one email" is a promise about a mailbox.
    """
    if not recipient or store.is_email_suppressed(recipient):
        return False
    carrier = parents[0]
    tz_name = _zone_for(carrier, zones if zones is not None else {})
    from utils import business_hours
    if not business_hours.is_business_time(now, tz_name):
        # S3: reminders go out inside the supplier's working hours. Outside
        # them the batch is HELD, not dropped — reminded_at stays NULL, so the
        # next in-hours run picks exactly these requests up. A 4-hour reminder
        # that lands at 02:00 is not a reminder, it is a reason to filter us.
        return False
    day = reminder_day(now, tz_name)
    if _reminded_on(recipient, day):
        return False
    if ceiling_reached(recipient, day):
        # S5: the mailbox has had its allowance today. The batch is HELD — no
        # parent is claimed, so every one of these requests is carried into the
        # next day's reminder. Nothing is discarded.
        return False
    claimed = [p for p in parents if store.mark_reminded(p["id"])]
    if not claimed:
        return False
    carrier = claimed[0]
    reminder = store.create_notification(
        kind=store.KIND_RFQ_REMINDER, account_id=carrier.get("account_id"),
        member_id=carrier.get("member_id"), subject_ref=carrier.get("subject_ref"),
        run_id=carrier.get("run_id"), supplier_domain=carrier.get("supplier_domain"),
        recipient=recipient,
        configuration_set=carrier.get("configuration_set"),
        detail={"reminder_for": carrier["id"],
                "batched": [p["id"] for p in claimed],
                # The day is stamped from the SUPPLIED instant, not read off
                # the row's wall-clock created_at: the per-day gate has to be
                # a function of the scheduler's ``now`` or a replay would see
                # a different answer than the original run.
                "day": day})
    if reminder is None:
        return False
    subject, body = _reminder_subject_and_body(claimed)
    _send_notification_mail(reminder, subject=subject, body=body,
                            recipient=recipient, mailed_day=day, at=now)
    return True


def _raise_escalation(parent: dict, *, day: Optional[str] = None) -> bool:
    """The end of the ladder: a concierge alert, and NO further email to the
    supplier (D6). Two unanswered mails is the point at which a human should
    take over, not the point at which to send a third.

    S6: the alert is deduplicated PER SUPPLIER ACCOUNT PER BUSINESS DAY, not
    per notification. Three unresponsive requests from one supplier on one day
    are ONE phone call, and three queue rows would be two pieces of work a
    human has to open before discovering they are the same conversation.

    ``mark_escalated`` stays per notification — that is what stops an
    individual request re-laddering — so the per-request guarantee is
    unchanged and only the human-visible count comes down. A coarser dedupe
    key can only reduce alerts, never produce a second one.
    """
    if not store.mark_escalated(parent["id"]):
        return False
    scope = parent.get("account_id") or parent.get("supplier_domain") \
        or parent["id"]
    store.raise_alert(
        kind=store.ALERT_RFQ_ESCALATION,
        dedupe_key=f"escalation:{scope}:{day}" if day
                   else f"escalation:{parent['id']}",
        account_id=parent.get("account_id"), member_id=parent.get("member_id"),
        notification_id=parent["id"], run_id=parent.get("run_id"),
        supplier_domain=parent.get("supplier_domain"),
        email=parent.get("recipient"),
        detail={"state": parent.get("state"), "reminded_at": parent.get("reminded_at"),
                "business_day": day})
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
        moment = now or _now()
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
                body=_digest_body(items), at=moment,
                # The digest IS S5's overflow destination, so it does not
                # consume a ceiling slot of its own: charging the member for
                # the mail that exists to carry their deferred items would
                # defer the deferral.
                mailed_day=None)
            # The batched items leave the deferred pool and are marked SENT:
            # they HAVE now been communicated, by the digest. Leaving them
            # QUEUED would make the escalation ladder chase mail that went out.
            for item in items:
                store.clear_deferred(item["id"])
                store.transition(item["id"], store.STATE_SENT,
                                 event_type="Digest", at=moment.isoformat(),
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
    """The admin QUEUE: open ACTION_NOW and QUEUE alerts, newest first (S6).

    DIGEST-tier alerts are deliberately absent. They are informational — a
    soft-bounce streak, a notification-cap block, a hard bounce on a contact
    who was not the account's last one — and interleaving them with work
    somebody has to do now is how a queue stops being read. They arrive once a
    day via :func:`run_concierge_digest`. ``[]`` when the flag is off.
    """
    if not notifications_active():
        return []
    return store.list_alerts(status=store.ALERT_OPEN, tiers=store.QUEUE_TIERS)


def run_concierge_digest(now: Optional[datetime] = None) -> dict:
    """The once-a-day read of the DIGEST tier (S6).

    A plain function over the store's state taking ``now`` as an argument, the
    same shape as every other scheduler entry point here. It REPORTS; it does
    not acknowledge, because "somebody was told" and "somebody dealt with it"
    are different facts and collapsing them loses the second one.

    Returns ``{"count": n, "alerts": [...]}``.
    """
    out: dict = {"count": 0, "alerts": []}
    if not notifications_active():
        return out
    try:
        moment = now or _now()
        alerts = store.list_alerts(status=store.ALERT_OPEN,
                                   tiers=(store.TIER_DIGEST,),
                                   day=moment.strftime("%Y-%m-%d"))
        out["alerts"] = alerts
        out["count"] = len(alerts)
    except Exception as exc:
        print(f"[Notifications] run_concierge_digest failed: {exc}")
    return out


def notification_actionability(now: Optional[datetime] = None, *,
                               days: int = 30) -> dict:
    """S7's rolling-window actionability report (``{}``-shaped when the flag
    is off). A thin pass-through so the API layer imports ONE module."""
    if not notifications_active():
        return {"window_days": days, "floor": 0.0, "as_of": None, "kinds": []}
    from utils import notification_metrics
    return notification_metrics.actionability(now or _now(), days=days)


def acknowledge_alert(alert_id: str, *, acknowledged_by: str) -> Optional[dict]:
    """Acknowledge one open alert. ``None`` when unknown / already
    acknowledged / the flag is off."""
    if not notifications_active():
        return None
    return store.acknowledge_alert(alert_id, acknowledged_by=acknowledged_by)
