"""
utils/notifications_store.py
Arc 4 T1 — persistence for the notification / delivery-tracking surface.

Convention B (the gate's G7 finding): a standalone raw-``sqlite3`` store with
its own module, its own ``data/*.sqlite``, ``_DATA_DIR`` / ``_DB_PATH`` module
attributes as the monkeypatch seam, ``CREATE TABLE IF NOT EXISTS`` on every
connection, uuid4 string PKs, ISO-8601-UTC TEXT timestamps, an ``is_test``
provenance column, ``contextlib.closing`` connections and fail-soft public
functions (``None`` / ``[]`` / ``False`` — never raise into a caller).

This module is DELIBERATELY FLAG-FREE. ``NOTIFICATIONS_V1`` is enforced one
layer up, in ``utils/notifications.py`` (the service) and in the api_server
route gates — the same split arc 2 used between ``supplier_accounts`` (store)
and its route gates, and it keeps the persistence round-trips testable without
flag plumbing. Nothing in the running system reaches this module with the flag
off, because nothing calls the service with the flag off.

FIVE INVARIANTS ARE ENFORCED HERE, not in the callers:

 1. **The state ladder is monotonic** (D3). ``QUEUED → SENT → DELIVERED →
    OPENED → CLICKED`` may only move forward; a late or out-of-order provider
    event never walks a notification backwards. ``can_transition`` is a pure
    predicate over ``_STATE_RANK`` — the same shape as the registry's
    ``_tier1_can_transition``, so it is table-testable with no I/O.
 2. **Terminal states are absorbing** (D3). ``BOUNCED / COMPLAINED / REJECTED /
    FAILED / SUPPRESSED`` accept no further transition, in or out.
 3. **Provider events are idempotent** (D4). A partial unique index on
    ``(provider_message_id, event_type)`` makes a redelivered SNS event a
    no-op at the persistence layer, so the webhook cannot double-apply even if
    its own guard were wrong.
 4. **A timestamp records what happened; ``state`` records how far it got.**
    Every newly-seen event stamps its own column (``clicked_at`` etc.) even
    when the ladder refuses the state move. D5 keys "seen" off ``clicked_at``,
    and a click that arrived after a bounce is still a click the supplier made
    — masking it would be a lie about a real human action.
 5. **One alert per deduplicated cause.** ``concierge_alerts.dedupe_key`` is
    UNIQUE (where present), so "raise the alert" is safe to call repeatedly
    from an idempotent scheduler run.

Suppression here is ADDRESS-level and lives in this store on purpose — see
the gate's FINDING F3: ``send_governance.suppression_add`` is DOMAIN-level and
permanent-until-admin, so routing one member's hard bounce into it would
silence every other member at that supplier plus all RFQ mail to them.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

# ---------------------------------------------------------------------------
# Vocabulary (D3) — kinds and states as module constants; nothing downstream
# spells a state as a bare literal.
# ---------------------------------------------------------------------------

KIND_RFQ_NEW = "RFQ_NEW"
KIND_RFQ_REMINDER = "RFQ_REMINDER"
KIND_RFQ_ESCALATION = "RFQ_ESCALATION"
KIND_RFQ_DIGEST = "RFQ_DIGEST"
KIND_AUTH_MAGIC_LINK = "AUTH_MAGIC_LINK"
KIND_MEMBER_INVITE = "MEMBER_INVITE"
KIND_TIER1_FYI = "TIER1_FYI"

KINDS: tuple[str, ...] = (
    KIND_RFQ_NEW, KIND_RFQ_REMINDER, KIND_RFQ_ESCALATION, KIND_RFQ_DIGEST,
    KIND_AUTH_MAGIC_LINK, KIND_MEMBER_INVITE, KIND_TIER1_FYI,
)

# Kinds that enter the D6 escalation ladder. RFQ_NEW only: a reminder is the
# ladder's own output, a digest is a batch of already-laddered items, auth and
# invite mail have no "unseen RFQ" to escalate, and TIER1_FYI is explicitly
# excluded by T4 (it has no sent_messages row, so nothing to view — it would
# escalate 100% of the time, which is noise, not signal).
ESCALATABLE_KINDS: frozenset[str] = frozenset({KIND_RFQ_NEW})

STATE_QUEUED = "QUEUED"
STATE_SENT = "SENT"
STATE_DELIVERED = "DELIVERED"
STATE_OPENED = "OPENED"
STATE_CLICKED = "CLICKED"
STATE_SUPPRESSED = "SUPPRESSED"
STATE_BOUNCED = "BOUNCED"
STATE_COMPLAINED = "COMPLAINED"
STATE_REJECTED = "REJECTED"
STATE_FAILED = "FAILED"

STATES: tuple[str, ...] = (
    STATE_QUEUED, STATE_SENT, STATE_DELIVERED, STATE_OPENED, STATE_CLICKED,
    STATE_SUPPRESSED, STATE_BOUNCED, STATE_COMPLAINED, STATE_REJECTED,
    STATE_FAILED,
)

# The progress ladder (D3). Rank is the WHOLE rule for forward motion: a
# transition is legal iff it strictly increases rank.
_STATE_RANK: dict[str, int] = {
    STATE_QUEUED: 0,
    STATE_SENT: 1,
    STATE_DELIVERED: 2,
    STATE_OPENED: 3,
    STATE_CLICKED: 4,
}

# Absorbing failures (D3): reachable from any non-terminal state, exited never.
TERMINAL_STATES: frozenset[str] = frozenset({
    STATE_SUPPRESSED, STATE_BOUNCED, STATE_COMPLAINED, STATE_REJECTED,
    STATE_FAILED,
})

# state -> the column stamped when the notification reaches it.
_STATE_TIMESTAMP_COLUMN: dict[str, str] = {
    STATE_QUEUED: "queued_at",
    STATE_SENT: "sent_at",
    STATE_DELIVERED: "delivered_at",
    STATE_OPENED: "opened_at",
    STATE_CLICKED: "clicked_at",
    STATE_SUPPRESSED: "suppressed_at",
    STATE_BOUNCED: "bounced_at",
    STATE_COMPLAINED: "complained_at",
    STATE_REJECTED: "rejected_at",
    STATE_FAILED: "failed_at",
}

PREF_IMMEDIATE = "IMMEDIATE"
PREF_DAILY_DIGEST = "DAILY_DIGEST"
PREF_NONE = "NONE"
PREFERENCES: tuple[str, ...] = (PREF_IMMEDIATE, PREF_DAILY_DIGEST, PREF_NONE)
DEFAULT_PREFERENCE = PREF_IMMEDIATE

ALERT_OPEN = "open"
ALERT_ACKNOWLEDGED = "acknowledged"

ALERT_RFQ_ESCALATION = "RFQ_ESCALATION"
ALERT_NO_NOTIFIABLE_MEMBERS = "NO_NOTIFIABLE_MEMBERS"
ALERT_EMAIL_SUPPRESSED = "EMAIL_SUPPRESSED"
ALERT_SOFT_BOUNCE_REPEATED = "SOFT_BOUNCE_REPEATED"
# Arc 4b R-F8: the notification daily cap suppressed a send. Informational,
# not an interruption — a sensibly sized cap should almost never fire, and the
# fix when it does is a config change, not a same-hour response. Deduped per
# UTC day by the caller's dedupe key, so a bad day raises one alert, not one
# per suppressed notification.
ALERT_NOTIFICATION_CAP_BLOCKED = "NOTIFICATION_CAP_BLOCKED"
# R7 (arc 5, F-03) — an auth-mail send refused for want of the tracking-off
# configuration set. Mirrors utils.mail_provider.ALERT_AUTH_MAIL_REFUSED.
ALERT_AUTH_MAIL_REFUSED = "AUTH_MAIL_REFUSED"

# Arc 4b S6 — alert TIERS. Only the top tier interrupts. A queue where every
# row is equally urgent is a queue with no urgency in it, and the first thing
# a human does with one is stop reading it.
#
#   ACTION_NOW  a person has to act TODAY: an account whose ONLY notifiable
#               contact just died (hard bounce / complaint), or an account
#               with a live RFQ and nobody to tell.
#   QUEUE       normal escalation work: a supplier unresponsive past one
#               business day on a request that still needs quotes.
#   DIGEST      informational, delivered once a day: soft-bounce streaks,
#               notification-cap blocks, a hard bounce on a contact who is not
#               the account's last one.
TIER_ACTION_NOW = "ACTION_NOW"
TIER_QUEUE = "QUEUE"
TIER_DIGEST = "DIGEST"
TIERS: tuple[str, ...] = (TIER_ACTION_NOW, TIER_QUEUE, TIER_DIGEST)

# The tiers the admin QUEUE shows. DIGEST is deliberately absent: it is read
# once a day in one place, not interleaved with work somebody has to do now.
QUEUE_TIERS: tuple[str, ...] = (TIER_ACTION_NOW, TIER_QUEUE)

# kind -> its DEFAULT tier. A caller may override per alert (a hard bounce is
# ACTION_NOW on an account's last contact and DIGEST on any other), which is
# why this is a default and not a lookup the raiser cannot argue with.
ALERT_TIERS: dict = {
    ALERT_RFQ_ESCALATION: TIER_QUEUE,
    ALERT_NO_NOTIFIABLE_MEMBERS: TIER_ACTION_NOW,
    ALERT_EMAIL_SUPPRESSED: TIER_ACTION_NOW,
    ALERT_SOFT_BOUNCE_REPEATED: TIER_DIGEST,
    ALERT_NOTIFICATION_CAP_BLOCKED: TIER_DIGEST,
    # R7 (arc 5, F-03): a refused auth-mail send is a misconfiguration that
    # silently stops suppliers signing in — somebody has to fix it now.
    ALERT_AUTH_MAIL_REFUSED: TIER_ACTION_NOW,
}


def alert_tier(kind: str, tier: Optional[str] = None) -> str:
    """The tier an alert lands in: the explicit one when it is a known tier,
    otherwise the kind's default, otherwise QUEUE. Pure; table-tested."""
    if tier in TIERS:
        return tier
    return ALERT_TIERS.get(kind, TIER_QUEUE)


def can_transition(current: Optional[str], nxt: str) -> bool:
    """Pure predicate: may a notification in ``current`` move to ``nxt``?

    The two D3 rules and nothing else:
      - a terminal current state absorbs (nothing leaves it);
      - a terminal ``nxt`` is reachable from any non-terminal state;
      - otherwise the ladder must strictly advance (equal rank is a replay,
        lower rank is an out-of-order provider event — both refused).

    Unknown states are refused (fail closed). No I/O; table-tested.
    """
    if nxt not in STATES:
        return False
    if current is not None and current in TERMINAL_STATES:
        return False
    if nxt in TERMINAL_STATES:
        return True
    if current is None:
        return nxt == STATE_QUEUED
    if current not in _STATE_RANK:
        return False
    return _STATE_RANK[nxt] > _STATE_RANK[current]


# ---------------------------------------------------------------------------
# Store plumbing (convention B)
# ---------------------------------------------------------------------------

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
_DB_PATH = os.path.join(_DATA_DIR, "notifications.sqlite")


_DDL_NOTIFICATIONS = """
CREATE TABLE IF NOT EXISTS notifications (
    id                  TEXT PRIMARY KEY,
    account_id          TEXT,
    member_id           TEXT,
    kind                TEXT NOT NULL,
    subject_ref         TEXT,
    run_id              TEXT,
    supplier_domain     TEXT,
    recipient           TEXT,
    channel             TEXT NOT NULL DEFAULT 'EMAIL',
    provider            TEXT,
    provider_message_id TEXT,
    configuration_set   TEXT,
    state               TEXT NOT NULL,
    deferred            INTEGER NOT NULL DEFAULT 0,
    -- Arc 4b S2: the instant this notification's coalescing window closes.
    -- Until then it waits so that RFQs arriving together become ONE email.
    -- NULL means "no window" (a reminder, a digest, an auth send).
    coalesce_until      TEXT,
    -- Arc 4b S5: the LOCAL business day on which this notification carried an
    -- actual email. It is what the per-member daily ceiling counts, and it is
    -- stamped from the scheduler's supplied instant, never from the wall
    -- clock, so a replay counts the same day the original run did.
    mailed_day          TEXT,
    -- Arc 4b S4: when the RFQ this describes was resolved, and by what. A
    -- cancelled notification leaves the ladder and the consolidated reminder.
    -- Recorded, never deleted: "we stopped chasing this, and here is why" has
    -- to stay answerable later.
    cancelled_at        TEXT,
    cancel_reason       TEXT,
    reminded_at         TEXT,
    escalated_at        TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT,
    queued_at           TEXT,
    sent_at             TEXT,
    delivered_at        TEXT,
    opened_at           TEXT,
    clicked_at          TEXT,
    suppressed_at       TEXT,
    bounced_at          TEXT,
    complained_at       TEXT,
    rejected_at         TEXT,
    failed_at           TEXT,
    detail_json         TEXT,
    is_test             INTEGER NOT NULL DEFAULT 0
);
"""

_DDL_EVENTS = """
CREATE TABLE IF NOT EXISTS notification_events (
    id                  TEXT PRIMARY KEY,
    notification_id     TEXT NOT NULL,
    event_type          TEXT NOT NULL,
    provider_message_id TEXT,
    from_state          TEXT,
    to_state            TEXT,
    applied             INTEGER NOT NULL DEFAULT 1,
    detail_json         TEXT,
    created_at          TEXT NOT NULL,
    is_test             INTEGER NOT NULL DEFAULT 0
);
"""

# D4's idempotency key. PARTIAL so that the locally-generated audit rows (a
# QUEUED write, a governance block) — which have no provider message id — are
# not forced into a single row per event type.
_INDEX_EVENT_IDEMPOTENCY = (
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_notification_events_idempotency "
    "ON notification_events (provider_message_id, event_type) "
    "WHERE provider_message_id IS NOT NULL"
)

_INDEX_EVENTS_NOTIFICATION = (
    "CREATE INDEX IF NOT EXISTS ix_notification_events_notification "
    "ON notification_events (notification_id)"
)

_INDEX_NOTIFICATIONS_PROVIDER_ID = (
    "CREATE INDEX IF NOT EXISTS ix_notifications_provider_message_id "
    "ON notifications (provider_message_id)"
)

_INDEX_NOTIFICATIONS_KIND_STATE = (
    "CREATE INDEX IF NOT EXISTS ix_notifications_kind_state "
    "ON notifications (kind, state)"
)

# D5. ``member_key`` exists ONLY to make the uniqueness work: SQLite treats
# NULLs as distinct in a UNIQUE index, so a token-door view (member_id NULL)
# would insert a fresh row on every poll. ``member_id`` stays nullable and is
# what readers see; ``member_key`` is its collapsed, never-NULL twin.
_DDL_RFQ_VIEWS = """
CREATE TABLE IF NOT EXISTS rfq_views (
    id               TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL,
    sent_message_id  TEXT,
    member_id        TEXT,
    member_key       TEXT NOT NULL,
    supplier_domain  TEXT NOT NULL,
    first_viewed_at  TEXT NOT NULL,
    last_viewed_at   TEXT NOT NULL,
    view_count       INTEGER NOT NULL DEFAULT 1,
    is_test          INTEGER NOT NULL DEFAULT 0,
    UNIQUE(run_id, member_key, supplier_domain)
);
"""

_DDL_PREFS = """
CREATE TABLE IF NOT EXISTS member_notification_prefs (
    member_id   TEXT PRIMARY KEY,
    account_id  TEXT,
    preference  TEXT NOT NULL DEFAULT 'IMMEDIATE',
    created_at  TEXT NOT NULL,
    updated_at  TEXT,
    is_test     INTEGER NOT NULL DEFAULT 0
);
"""

_DDL_ALERTS = """
CREATE TABLE IF NOT EXISTS concierge_alerts (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    dedupe_key      TEXT,
    account_id      TEXT,
    member_id       TEXT,
    notification_id TEXT,
    run_id          TEXT,
    supplier_domain TEXT,
    email           TEXT,
    detail_json     TEXT,
    status          TEXT NOT NULL DEFAULT 'open',
    tier            TEXT NOT NULL DEFAULT 'QUEUE',
    created_at      TEXT NOT NULL,
    acknowledged_at TEXT,
    acknowledged_by TEXT,
    is_test         INTEGER NOT NULL DEFAULT 0
);
"""

_INDEX_ALERT_DEDUPE = (
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_concierge_alerts_dedupe "
    "ON concierge_alerts (dedupe_key) WHERE dedupe_key IS NOT NULL"
)

# D8 / F3: ADDRESS-level suppression, this store's own — never the governance
# store's domain-level one.
_DDL_SUPPRESSION = """
CREATE TABLE IF NOT EXISTS email_suppression (
    email         TEXT PRIMARY KEY,
    reason        TEXT,
    member_id     TEXT,
    account_id    TEXT,
    detail_json   TEXT,
    suppressed_at TEXT NOT NULL,
    is_test       INTEGER NOT NULL DEFAULT 0
);
"""

# Soft bounces do NOT suppress (D8); three consecutive ones raise an alert.
# "Consecutive" is why the counter resets on a delivery. ``first_at`` stamps the
# START of the current streak: it is what makes one alert per streak possible
# (an alert deduped on the address alone could never fire for a second streak,
# and one deduped on the count alone fires again on every later bounce).
_DDL_SOFT_BOUNCES = """
CREATE TABLE IF NOT EXISTS soft_bounce_counts (
    email    TEXT PRIMARY KEY,
    count    INTEGER NOT NULL DEFAULT 0,
    last_at  TEXT,
    first_at TEXT
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """PRAGMA-driven idempotent column adds (convention B, G7).

    Only for tables whose shape changed after they first shipped — a fresh
    database gets the column from the DDL above and this is a no-op.
    """
    have = {r[1] for r in conn.execute("PRAGMA table_info(soft_bounce_counts)")}
    if have and "first_at" not in have:
        conn.execute("ALTER TABLE soft_bounce_counts ADD COLUMN first_at TEXT")
    alerts = {r[1] for r in conn.execute("PRAGMA table_info(concierge_alerts)")}
    if alerts and "tier" not in alerts:
        # Arc 4b S6. Legacy rows default to QUEUE — the tier the arc-4 queue
        # behaved as — so an alert raised before tiers existed keeps appearing
        # exactly where the person watching it expects.
        conn.execute("ALTER TABLE concierge_alerts ADD COLUMN tier TEXT")
        conn.execute("UPDATE concierge_alerts SET tier = ? WHERE tier IS NULL",
                     (TIER_QUEUE,))
    notif = {r[1] for r in conn.execute("PRAGMA table_info(notifications)")}
    for column, ddl in (("coalesce_until", "coalesce_until TEXT"),
                        ("cancelled_at", "cancelled_at TEXT"),
                        ("cancel_reason", "cancel_reason TEXT"),
                        ("mailed_day", "mailed_day TEXT")):
        if notif and column not in notif:
            conn.execute(f"ALTER TABLE notifications ADD COLUMN {ddl}")


def _get_conn() -> sqlite3.Connection:
    os.makedirs(_DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.execute(_DDL_NOTIFICATIONS)
    conn.execute(_INDEX_NOTIFICATIONS_PROVIDER_ID)
    conn.execute(_INDEX_NOTIFICATIONS_KIND_STATE)
    conn.execute(_DDL_EVENTS)
    conn.execute(_INDEX_EVENT_IDEMPOTENCY)
    conn.execute(_INDEX_EVENTS_NOTIFICATION)
    conn.execute(_DDL_RFQ_VIEWS)
    conn.execute(_DDL_PREFS)
    conn.execute(_DDL_ALERTS)
    conn.execute(_INDEX_ALERT_DEDUPE)
    conn.execute(_DDL_SUPPRESSION)
    conn.execute(_DDL_SOFT_BOUNCES)
    _migrate(conn)
    conn.commit()
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(r: sqlite3.Row) -> dict:
    out = dict(r)
    raw = out.pop("detail_json", None)
    if raw is not None:
        try:
            out["detail"] = json.loads(raw)
        except (ValueError, TypeError):
            out["detail"] = None
    return out


def _dumps(detail: Optional[dict]) -> Optional[str]:
    if detail is None:
        return None
    try:
        return json.dumps(detail, default=str)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Notifications (D3)
# ---------------------------------------------------------------------------

def create_notification(
    *,
    kind: str,
    account_id: Optional[str] = None,
    member_id: Optional[str] = None,
    subject_ref: Optional[str] = None,
    run_id: Optional[str] = None,
    supplier_domain: Optional[str] = None,
    recipient: Optional[str] = None,
    provider: Optional[str] = None,
    configuration_set: Optional[str] = None,
    deferred: bool = False,
    coalesce_until: Optional[str] = None,
    detail: Optional[dict] = None,
    is_test: bool = False,
) -> Optional[dict]:
    """Create one notification at ``QUEUED`` and audit the creation.

    Every notification starts QUEUED — even one that is about to be handed
    straight to a provider — so a crash between "decided to notify" and "the
    provider answered" leaves an auditable QUEUED row rather than nothing.
    That is the same record-before-attempt discipline ``rfq_send`` uses for
    the ``sent_messages`` ledger.

    Returns the created row, or ``None`` on bad input / store failure.
    """
    if kind not in KINDS:
        print(f"[Notifications] create_notification refused unknown kind {kind!r}")
        return None
    nid = str(uuid.uuid4())
    now = _now()
    try:
        with closing(_get_conn()) as conn:
            conn.execute(
                """INSERT INTO notifications
                   (id, account_id, member_id, kind, subject_ref, run_id,
                    supplier_domain, recipient, channel, provider,
                    configuration_set, state, deferred, coalesce_until,
                    created_at, updated_at, queued_at, detail_json, is_test)
                   VALUES (?,?,?,?,?,?,?,?, 'EMAIL', ?,?,?,?,?,?,?,?,?,?)""",
                (nid, account_id, member_id, kind, subject_ref, run_id,
                 supplier_domain, recipient, provider, configuration_set,
                 STATE_QUEUED, 1 if deferred else 0, coalesce_until, now, now,
                 now, _dumps(detail), 1 if is_test else 0),
            )
            conn.commit()
    except Exception as exc:
        print(f"[Notifications] create_notification failed for {kind}: {exc}")
        return None
    _record_event(nid, event_type="Queued", from_state=None,
                  to_state=STATE_QUEUED, applied=True, is_test=is_test)
    return get_notification(nid)


def get_notification(notification_id: str) -> Optional[dict]:
    """One notification by id, or ``None`` (fail-soft)."""
    if not notification_id:
        return None
    try:
        with closing(_get_conn()) as conn:
            conn.row_factory = sqlite3.Row
            r = conn.execute("SELECT * FROM notifications WHERE id = ?",
                             (notification_id,)).fetchone()
            return _row(r) if r else None
    except Exception as exc:
        print(f"[Notifications] get_notification failed for {notification_id!r}: {exc}")
        return None


def get_notification_by_provider_message_id(provider_message_id: str) -> Optional[dict]:
    """The notification a provider event belongs to, resolved by the id the
    provider returned at send time. ``None`` when the event is for a message
    this system did not send (a stale topic, another app on the same SNS
    topic) — the webhook treats that as a silent no-op, never an error that
    would let a caller probe for message ids."""
    if not provider_message_id:
        return None
    try:
        with closing(_get_conn()) as conn:
            conn.row_factory = sqlite3.Row
            r = conn.execute(
                "SELECT * FROM notifications WHERE provider_message_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (provider_message_id,)).fetchone()
            return _row(r) if r else None
    except Exception as exc:
        print(f"[Notifications] lookup by provider id failed: {exc}")
        return None


def list_notifications(*, kind: Optional[str] = None,
                       kinds: Optional[Iterable[str]] = None,
                       state: Optional[str] = None,
                       member_id: Optional[str] = None,
                       recipient: Optional[str] = None,
                       deferred: Optional[bool] = None,
                       cancelled: Optional[bool] = None,
                       run_id: Optional[str] = None) -> list[dict]:
    """Notifications matching every supplied filter, oldest first (the
    scheduler wants the oldest unseen RFQ first). ``[]`` on fail-soft."""
    where: list[str] = []
    args: list[Any] = []
    if kind:
        where.append("kind = ?")
        args.append(kind)
    if kinds:
        ks = list(kinds)
        if not ks:
            return []
        where.append(f"kind IN ({','.join('?' for _ in ks)})")
        args.extend(ks)
    if state:
        where.append("state = ?")
        args.append(state)
    if member_id:
        where.append("member_id = ?")
        args.append(member_id)
    if recipient:
        where.append("recipient = ?")
        args.append(recipient)
    if cancelled is not None:
        where.append("cancelled_at IS NOT NULL" if cancelled
                     else "cancelled_at IS NULL")
    if run_id:
        where.append("run_id = ?")
        args.append(run_id)
    if deferred is not None:
        where.append("deferred = ?")
        args.append(1 if deferred else 0)
    sql = "SELECT * FROM notifications"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at"
    try:
        with closing(_get_conn()) as conn:
            conn.row_factory = sqlite3.Row
            return [_row(r) for r in conn.execute(sql, tuple(args)).fetchall()]
    except Exception as exc:
        print(f"[Notifications] list_notifications failed: {exc}")
        return []


def count_notifications_on_day(*, kind: str, day: str,
                               account_id: Optional[str] = None,
                               supplier_domain: Optional[str] = None,
                               recipient: Optional[str] = None) -> int:
    """How many notifications of ``kind`` were created on one UTC day.

    The per-account invite cap (arc 4b R-F5) counts with this. ``day`` is
    ``'YYYY-MM-DD'`` and is a PARAMETER: no wall-clock read happens here, so
    the count a caller gets is a function of the instant it supplied.

    Fail-soft ``0``. A store failure must not silently *block* invites (the
    caller would refuse a legitimate one) — the cap fails OPEN here because
    the ledger and the governance auth cap still stand behind it.
    """
    where = ["kind = ?", "substr(created_at, 1, 10) = ?"]
    args: list[Any] = [kind, day]
    if account_id:
        where.append("account_id = ?")
        args.append(account_id)
    if supplier_domain:
        where.append("supplier_domain = ?")
        args.append(supplier_domain)
    if recipient:
        where.append("recipient = ?")
        args.append(recipient)
    try:
        with closing(_get_conn()) as conn:
            r = conn.execute(
                f"SELECT COUNT(*) FROM notifications WHERE {' AND '.join(where)}",
                tuple(args)).fetchone()
            return int(r[0]) if r else 0
    except Exception as exc:
        print(f"[Notifications] count_notifications_on_day failed: {exc}")
        return 0


def set_provider_message_id(notification_id: str, provider_message_id: str,
                            *, provider: Optional[str] = None) -> bool:
    """Bind the provider's message id to a notification (the join key every
    later SNS event arrives on). Fail-soft ``False``."""
    if not notification_id or not provider_message_id:
        return False
    try:
        with closing(_get_conn()) as conn:
            cur = conn.execute(
                "UPDATE notifications SET provider_message_id = ?, "
                "provider = COALESCE(?, provider), updated_at = ? WHERE id = ?",
                (provider_message_id, provider, _now(), notification_id))
            conn.commit()
            return cur.rowcount > 0
    except Exception as exc:
        print(f"[Notifications] set_provider_message_id failed: {exc}")
        return False


def transition(notification_id: str, to_state: str, *,
               event_type: Optional[str] = None,
               provider_message_id: Optional[str] = None,
               detail: Optional[dict] = None,
               at: Optional[str] = None) -> Optional[dict]:
    """Apply one state transition, writing an audit row either way (D3).

    The ladder decides whether ``state`` moves; the event's own timestamp
    column is stamped regardless (invariant 4 in the module docstring). A
    refused transition is NOT an error — it is a late or duplicate provider
    event, and it is recorded with ``applied=0`` so the audit shows what the
    provider claimed as well as what the system accepted.

    Returns the notification as it now stands, or ``None`` if it is unknown.
    """
    current = get_notification(notification_id)
    if current is None:
        return None
    if to_state not in STATES:
        print(f"[Notifications] transition refused unknown state {to_state!r}")
        return current
    from_state = current.get("state")
    allowed = can_transition(from_state, to_state)
    stamp = at or _now()
    column = _STATE_TIMESTAMP_COLUMN.get(to_state)
    sets: list[str] = ["updated_at = ?"]
    args: list[Any] = [stamp]
    if column and not current.get(column):
        sets.append(f"{column} = ?")
        args.append(stamp)
    if allowed:
        sets.append("state = ?")
        args.append(to_state)
    try:
        with closing(_get_conn()) as conn:
            conn.execute(f"UPDATE notifications SET {', '.join(sets)} WHERE id = ?",
                         (*args, notification_id))
            conn.commit()
    except Exception as exc:
        print(f"[Notifications] transition failed for {notification_id!r}: {exc}")
        return current
    _record_event(notification_id,
                  event_type=event_type or to_state,
                  provider_message_id=provider_message_id,
                  from_state=from_state, to_state=to_state, applied=allowed,
                  detail=detail, is_test=bool(current.get("is_test")))
    return get_notification(notification_id)


def mark_reminded(notification_id: str, *, at: Optional[str] = None) -> bool:
    """Stamp ``reminded_at`` — the D6 "at most one reminder per RFQ per
    member" guard. The ``reminded_at IS NULL`` predicate makes the write
    itself the guard: a second call changes no row and returns ``False``,
    even if two schedulers race."""
    try:
        with closing(_get_conn()) as conn:
            cur = conn.execute(
                "UPDATE notifications SET reminded_at = ?, updated_at = ? "
                "WHERE id = ? AND reminded_at IS NULL",
                (at or _now(), _now(), notification_id))
            conn.commit()
            return cur.rowcount > 0
    except Exception as exc:
        print(f"[Notifications] mark_reminded failed: {exc}")
        return False


def mark_escalated(notification_id: str, *, at: Optional[str] = None) -> bool:
    """Stamp ``escalated_at`` once (same write-is-the-guard shape as
    ``mark_reminded``)."""
    try:
        with closing(_get_conn()) as conn:
            cur = conn.execute(
                "UPDATE notifications SET escalated_at = ?, updated_at = ? "
                "WHERE id = ? AND escalated_at IS NULL",
                (at or _now(), _now(), notification_id))
            conn.commit()
            return cur.rowcount > 0
    except Exception as exc:
        print(f"[Notifications] mark_escalated failed: {exc}")
        return False


def clear_coalesce(notification_id: str) -> bool:
    """Close a notification's coalescing window (arc 4b S2) — called once the
    batch carrying it has been handed to the transport, so it can never be
    picked up by a second flush. Fail-soft ``False``."""
    try:
        with closing(_get_conn()) as conn:
            cur = conn.execute(
                "UPDATE notifications SET coalesce_until = NULL, updated_at = ? "
                "WHERE id = ?", (_now(), notification_id))
            conn.commit()
            return cur.rowcount > 0
    except Exception as exc:
        print(f"[Notifications] clear_coalesce failed: {exc}")
        return False


def mark_mailed(notification_id: str, day: str) -> bool:
    """Stamp the LOCAL business day on which this notification carried a real
    email (arc 4b S5). The per-member daily ceiling counts these, so exactly
    one row per email is stamped — the batch's carrier, not every notification
    the batch covered, because the promise is about emails received.
    Fail-soft ``False``."""
    if not notification_id or not day:
        return False
    try:
        with closing(_get_conn()) as conn:
            cur = conn.execute(
                "UPDATE notifications SET mailed_day = ?, updated_at = ? "
                "WHERE id = ?", (day, _now(), notification_id))
            conn.commit()
            return cur.rowcount > 0
    except Exception as exc:
        print(f"[Notifications] mark_mailed failed: {exc}")
        return False


def count_mailed_on_day(recipient: str, day: str) -> int:
    """How many notification EMAILS this mailbox has been sent on ``day``.

    ``day`` is a parameter (the account's local business day), so the ceiling
    is a function of the scheduler's supplied instant and never of the wall
    clock. Fail-soft ``0``: a store failure must not silently gag a member.
    """
    if not recipient or not day:
        return 0
    try:
        with closing(_get_conn()) as conn:
            r = conn.execute(
                "SELECT COUNT(*) FROM notifications WHERE recipient = ? "
                "AND mailed_day = ?", (recipient, day)).fetchone()
            return int(r[0]) if r else 0
    except Exception as exc:
        print(f"[Notifications] count_mailed_on_day failed: {exc}")
        return 0


def set_deferred(notification_id: str) -> bool:
    """Push a notification into the digest pool (arc 4b S5's overflow).

    The counterpart of ``clear_deferred``. This is how the daily ceiling
    DEFERS rather than discards: the row keeps its QUEUED state and its
    unseen-ness, and the next digest carries it.
    """
    try:
        with closing(_get_conn()) as conn:
            cur = conn.execute(
                "UPDATE notifications SET deferred = 1, coalesce_until = NULL, "
                "updated_at = ? WHERE id = ?", (_now(), notification_id))
            conn.commit()
            return cur.rowcount > 0
    except Exception as exc:
        print(f"[Notifications] set_deferred failed: {exc}")
        return False


def mark_cancelled(notification_id: str, *, reason: str,
                   at: Optional[str] = None) -> bool:
    """Arc 4b S4: the RFQ this notification describes has been resolved, so
    stop chasing it.

    The ``cancelled_at IS NULL`` predicate makes the write its own guard, the
    same shape as ``mark_reminded`` — a second cancellation changes no row.
    An audit event is appended either way the caller can read back, because
    "we stopped chasing this, and why" must not be a silent state change.
    Fail-soft ``False``.
    """
    try:
        with closing(_get_conn()) as conn:
            cur = conn.execute(
                "UPDATE notifications SET cancelled_at = ?, cancel_reason = ?, "
                "updated_at = ? WHERE id = ? AND cancelled_at IS NULL",
                (at or _now(), reason, _now(), notification_id))
            conn.commit()
            if cur.rowcount == 0:
                return False
    except Exception as exc:
        print(f"[Notifications] mark_cancelled failed: {exc}")
        return False
    _record_event(notification_id, event_type="Cancelled",
                  detail={"reason": reason})
    return True


def clear_deferred(notification_id: str) -> bool:
    """Un-defer a DAILY_DIGEST notification once the digest carrying it has
    gone out. Fail-soft ``False``."""
    try:
        with closing(_get_conn()) as conn:
            cur = conn.execute(
                "UPDATE notifications SET deferred = 0, updated_at = ? WHERE id = ?",
                (_now(), notification_id))
            conn.commit()
            return cur.rowcount > 0
    except Exception as exc:
        print(f"[Notifications] clear_deferred failed: {exc}")
        return False


# ---------------------------------------------------------------------------
# Notification events (D3 audit + D4 idempotency)
# ---------------------------------------------------------------------------

def _record_event(notification_id: str, *, event_type: str,
                  provider_message_id: Optional[str] = None,
                  from_state: Optional[str] = None,
                  to_state: Optional[str] = None,
                  applied: bool = True,
                  detail: Optional[dict] = None,
                  is_test: bool = False) -> Optional[str]:
    """Append one audit row. Returns its id, or ``None`` when the idempotency
    index rejected it (a replayed provider event) or the store failed."""
    eid = str(uuid.uuid4())
    try:
        with closing(_get_conn()) as conn:
            conn.execute(
                """INSERT INTO notification_events
                   (id, notification_id, event_type, provider_message_id,
                    from_state, to_state, applied, detail_json, created_at, is_test)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (eid, notification_id, event_type, provider_message_id,
                 from_state, to_state, 1 if applied else 0, _dumps(detail),
                 _now(), 1 if is_test else 0))
            conn.commit()
        return eid
    except sqlite3.IntegrityError:
        # The D4 idempotency index fired: this exact provider event already
        # has an audit row. Not an error — the redelivery is a no-op.
        return None
    except Exception as exc:
        print(f"[Notifications] event write failed for {notification_id!r}: {exc}")
        return None


def claim_provider_event(provider_message_id: str, event_type: str) -> bool:
    """D4's idempotency claim. ``True`` the FIRST time this (message id, event
    type) pair is seen, ``False`` for every redelivery.

    The claim is the INSERT itself — a UNIQUE-index rejection, not a read-then-
    write — so two concurrent SNS deliveries of the same event cannot both win.
    Callers apply the event only on ``True``.
    """
    if not provider_message_id or not event_type:
        return False
    try:
        with closing(_get_conn()) as conn:
            conn.execute(
                """INSERT INTO notification_events
                   (id, notification_id, event_type, provider_message_id,
                    from_state, to_state, applied, detail_json, created_at, is_test)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), "", f"claim:{event_type}", provider_message_id,
                 None, None, 0, None, _now(), 0))
            conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    except Exception as exc:
        print(f"[Notifications] claim_provider_event failed: {exc}")
        return False


def list_events(notification_id: str) -> list[dict]:
    """Every audit row for one notification, oldest first. ``[]`` fail-soft."""
    try:
        with closing(_get_conn()) as conn:
            conn.row_factory = sqlite3.Row
            return [_row(r) for r in conn.execute(
                "SELECT * FROM notification_events WHERE notification_id = ? "
                "ORDER BY created_at, id", (notification_id,)).fetchall()]
    except Exception as exc:
        print(f"[Notifications] list_events failed: {exc}")
        return []


# ---------------------------------------------------------------------------
# RfqView (D5 — the strong "seen" signal)
# ---------------------------------------------------------------------------

def record_rfq_view(*, run_id: str, supplier_domain: str,
                    member_id: Optional[str] = None,
                    sent_message_id: Optional[str] = None,
                    at: Optional[str] = None,
                    is_test: bool = False) -> Optional[dict]:
    """Record that this credential viewed this RFQ.

    First view inserts with ``first_viewed_at == last_viewed_at``; every later
    view updates ``last_viewed_at`` and bumps ``view_count`` — ``first_viewed_at``
    is never rewritten, because "when did they first see it" is exactly the
    fact D6's ladder is judged against. Fail-soft ``None``.
    """
    if not run_id or not supplier_domain:
        return None
    now = at or _now()
    key = member_id or ""
    try:
        with closing(_get_conn()) as conn:
            cur = conn.execute(
                "UPDATE rfq_views SET last_viewed_at = ?, view_count = view_count + 1, "
                "sent_message_id = COALESCE(sent_message_id, ?) "
                "WHERE run_id = ? AND member_key = ? AND supplier_domain = ?",
                (now, sent_message_id, run_id, key, supplier_domain))
            if cur.rowcount == 0:
                conn.execute(
                    """INSERT INTO rfq_views
                       (id, run_id, sent_message_id, member_id, member_key,
                        supplier_domain, first_viewed_at, last_viewed_at,
                        view_count, is_test)
                       VALUES (?,?,?,?,?,?,?,?,1,?)""",
                    (str(uuid.uuid4()), run_id, sent_message_id, member_id, key,
                     supplier_domain, now, now, 1 if is_test else 0))
            conn.commit()
    except Exception as exc:
        print(f"[Notifications] record_rfq_view failed for {run_id!r}: {exc}")
        return None
    return get_rfq_view(run_id=run_id, supplier_domain=supplier_domain,
                        member_id=member_id)


def get_rfq_view(*, run_id: str, supplier_domain: str,
                 member_id: Optional[str] = None) -> Optional[dict]:
    """The view row for one (run, credential, domain), or ``None``."""
    try:
        with closing(_get_conn()) as conn:
            conn.row_factory = sqlite3.Row
            r = conn.execute(
                "SELECT * FROM rfq_views WHERE run_id = ? AND member_key = ? "
                "AND supplier_domain = ?",
                (run_id, member_id or "", supplier_domain)).fetchone()
            return _row(r) if r else None
    except Exception as exc:
        print(f"[Notifications] get_rfq_view failed: {exc}")
        return None


def list_rfq_views(run_id: str, *, supplier_domain: Optional[str] = None
                   ) -> list[dict]:
    """Every view row for one run (optionally one supplier), oldest first.

    T11 needs the FIRST view instant, not just "was it viewed", because the
    actionability measure is "did they look within one business day" — a view
    three weeks later is a real view and a failed notification. ``[]``
    fail-soft.
    """
    if not run_id:
        return []
    where = ["run_id = ?"]
    args: list[Any] = [run_id]
    if supplier_domain:
        where.append("supplier_domain = ?")
        args.append(supplier_domain)
    try:
        with closing(_get_conn()) as conn:
            conn.row_factory = sqlite3.Row
            return [_row(r) for r in conn.execute(
                f"SELECT * FROM rfq_views WHERE {' AND '.join(where)} "
                f"ORDER BY first_viewed_at", tuple(args)).fetchall()]
    except Exception as exc:
        print(f"[Notifications] list_rfq_views failed: {exc}")
        return []


def rfq_viewed(run_id: str, *, member_id: Optional[str] = None,
               supplier_domain: Optional[str] = None) -> bool:
    """D5's "seen in the portal" predicate.

    Deliberately BROADER than ``get_rfq_view``: a view by ANY credential at
    the supplier — the named member, a colleague, or the claim-token door with
    no member at all — means the company has seen the request. Escalating to a
    human because the specific addressee did not personally click, while their
    colleague already opened it, would be the ladder crying wolf.
    """
    if not run_id:
        return False
    where = ["run_id = ?"]
    args: list[Any] = [run_id]
    if supplier_domain:
        where.append("supplier_domain = ?")
        args.append(supplier_domain)
    elif member_id:
        where.append("member_key = ?")
        args.append(member_id)
    try:
        with closing(_get_conn()) as conn:
            r = conn.execute(
                f"SELECT 1 FROM rfq_views WHERE {' AND '.join(where)} LIMIT 1",
                tuple(args)).fetchone()
            return r is not None
    except Exception as exc:
        print(f"[Notifications] rfq_viewed failed: {exc}")
        return False


def viewed_run_ids(supplier_domain: str) -> set[str]:
    """Every run this supplier domain has viewed — one query for the inbox's
    unseen indicator (T11) rather than one per row. ``set()`` fail-soft."""
    if not supplier_domain:
        return set()
    try:
        with closing(_get_conn()) as conn:
            rows = conn.execute(
                "SELECT DISTINCT run_id FROM rfq_views WHERE supplier_domain = ?",
                (supplier_domain,)).fetchall()
            return {r[0] for r in rows if r[0]}
    except Exception as exc:
        print(f"[Notifications] viewed_run_ids failed: {exc}")
        return set()


# ---------------------------------------------------------------------------
# Member preferences (D7)
# ---------------------------------------------------------------------------

def get_preference(member_id: str) -> str:
    """A member's notification preference, defaulting to ``IMMEDIATE`` (D7).

    An absent row, an unrecognised stored value and a store failure ALL read
    as IMMEDIATE. The failure direction matters: the fallback must be "notify
    them", never "silently stop notifying them" — the product promise is that
    a supplier never misses an RFQ, so a broken store must not become an
    accidental opt-out.
    """
    if not member_id:
        return DEFAULT_PREFERENCE
    try:
        with closing(_get_conn()) as conn:
            r = conn.execute(
                "SELECT preference FROM member_notification_prefs WHERE member_id = ?",
                (member_id,)).fetchone()
            if r and r[0] in PREFERENCES:
                return r[0]
            return DEFAULT_PREFERENCE
    except Exception as exc:
        print(f"[Notifications] get_preference failed for {member_id!r}: {exc}")
        return DEFAULT_PREFERENCE


def set_preference(member_id: str, preference: str, *,
                   account_id: Optional[str] = None,
                   is_test: bool = False) -> Optional[str]:
    """Upsert a member's preference. Returns the stored value, or ``None``
    for an unknown preference / bad input / store failure."""
    if not member_id or preference not in PREFERENCES:
        return None
    now = _now()
    try:
        with closing(_get_conn()) as conn:
            conn.execute(
                """INSERT INTO member_notification_prefs
                   (member_id, account_id, preference, created_at, updated_at, is_test)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(member_id) DO UPDATE SET
                     preference = excluded.preference,
                     account_id = COALESCE(excluded.account_id, member_notification_prefs.account_id),
                     updated_at = excluded.updated_at""",
                (member_id, account_id, preference, now, now, 1 if is_test else 0))
            conn.commit()
        return preference
    except Exception as exc:
        print(f"[Notifications] set_preference failed for {member_id!r}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Concierge alerts (D6/D7/D8 — the human-visible end of every escalation)
# ---------------------------------------------------------------------------

def raise_alert(*, kind: str, dedupe_key: Optional[str] = None,
                tier: Optional[str] = None,
                account_id: Optional[str] = None,
                member_id: Optional[str] = None,
                notification_id: Optional[str] = None,
                run_id: Optional[str] = None,
                supplier_domain: Optional[str] = None,
                email: Optional[str] = None,
                detail: Optional[dict] = None,
                is_test: bool = False) -> Optional[dict]:
    """Raise one concierge alert, deduplicated on ``dedupe_key``.

    Returns the new alert, or ``None`` when an alert with the same dedupe key
    already exists (the caller treats that as "already raised", not failure) —
    which is what makes ``run_escalations`` safe to run twice with the same
    ``now``.
    """
    aid = str(uuid.uuid4())
    try:
        with closing(_get_conn()) as conn:
            conn.execute(
                """INSERT INTO concierge_alerts
                   (id, kind, dedupe_key, account_id, member_id, notification_id,
                    run_id, supplier_domain, email, detail_json, status, tier,
                    created_at, is_test)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (aid, kind, dedupe_key, account_id, member_id, notification_id,
                 run_id, supplier_domain, email, _dumps(detail), ALERT_OPEN,
                 alert_tier(kind, tier), _now(), 1 if is_test else 0))
            conn.commit()
    except sqlite3.IntegrityError:
        return None          # already raised — the dedupe index did its job
    except Exception as exc:
        print(f"[Notifications] raise_alert failed for {kind}: {exc}")
        return None
    return get_alert(aid)


def get_alert(alert_id: str) -> Optional[dict]:
    """One alert by id, or ``None``."""
    try:
        with closing(_get_conn()) as conn:
            conn.row_factory = sqlite3.Row
            r = conn.execute("SELECT * FROM concierge_alerts WHERE id = ?",
                             (alert_id,)).fetchone()
            return _row(r) if r else None
    except Exception as exc:
        print(f"[Notifications] get_alert failed: {exc}")
        return None


def list_alerts(*, status: Optional[str] = ALERT_OPEN,
                kind: Optional[str] = None,
                tiers: Optional[Iterable[str]] = None,
                day: Optional[str] = None) -> list[dict]:
    """Alerts, newest first. ``status=None`` lists every status; ``tiers``
    restricts to a set of S6 tiers; ``day`` restricts to one UTC creation day
    (the concierge digest's window). ``[]`` fail-soft."""
    where: list[str] = []
    args: list[Any] = []
    if status:
        where.append("status = ?")
        args.append(status)
    if kind:
        where.append("kind = ?")
        args.append(kind)
    if tiers is not None:
        wanted = list(tiers)
        if not wanted:
            return []
        where.append(f"tier IN ({','.join('?' for _ in wanted)})")
        args.extend(wanted)
    if day:
        where.append("substr(created_at, 1, 10) = ?")
        args.append(day)
    sql = "SELECT * FROM concierge_alerts"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC"
    try:
        with closing(_get_conn()) as conn:
            conn.row_factory = sqlite3.Row
            return [_row(r) for r in conn.execute(sql, tuple(args)).fetchall()]
    except Exception as exc:
        print(f"[Notifications] list_alerts failed: {exc}")
        return []


def acknowledge_alert(alert_id: str, *, acknowledged_by: str) -> Optional[dict]:
    """Acknowledge an OPEN alert — a status flip, never a delete (the
    ``unmatched_reply`` dismiss convention, G6). ``None`` when the alert is
    unknown or already acknowledged; the caller maps that to 404/409."""
    try:
        with closing(_get_conn()) as conn:
            cur = conn.execute(
                "UPDATE concierge_alerts SET status = ?, acknowledged_at = ?, "
                "acknowledged_by = ? WHERE id = ? AND status = ?",
                (ALERT_ACKNOWLEDGED, _now(), acknowledged_by, alert_id, ALERT_OPEN))
            conn.commit()
            if cur.rowcount == 0:
                return None
    except Exception as exc:
        print(f"[Notifications] acknowledge_alert failed: {exc}")
        return None
    return get_alert(alert_id)


# ---------------------------------------------------------------------------
# Address-level suppression + soft-bounce counting (D8 / gate FINDING F3)
# ---------------------------------------------------------------------------

def suppress_email(email: str, *, reason: str,
                   member_id: Optional[str] = None,
                   account_id: Optional[str] = None,
                   detail: Optional[dict] = None,
                   is_test: bool = False) -> bool:
    """Suppress ONE address (not the domain — F3). Idempotent upsert."""
    norm = (email or "").strip().lower()
    if not norm:
        return False
    try:
        with closing(_get_conn()) as conn:
            conn.execute(
                """INSERT INTO email_suppression
                   (email, reason, member_id, account_id, detail_json,
                    suppressed_at, is_test)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(email) DO UPDATE SET
                     reason = excluded.reason,
                     suppressed_at = excluded.suppressed_at""",
                (norm, reason, member_id, account_id, _dumps(detail),
                 _now(), 1 if is_test else 0))
            conn.commit()
        return True
    except Exception as exc:
        print(f"[Notifications] suppress_email failed: {exc}")
        return False


def is_email_suppressed(email: str) -> bool:
    """True iff this address is suppressed. A store failure reads as NOT
    suppressed — deliberately the opposite direction to ``get_preference``'s
    fallback, and for the same reason: this predicate only ever *removes* a
    recipient, so failing closed here would silently mute a whole account."""
    norm = (email or "").strip().lower()
    if not norm:
        return False
    try:
        with closing(_get_conn()) as conn:
            r = conn.execute("SELECT 1 FROM email_suppression WHERE email = ?",
                             (norm,)).fetchone()
            return r is not None
    except Exception as exc:
        print(f"[Notifications] is_email_suppressed failed: {exc}")
        return False


def list_suppressed_emails() -> list[dict]:
    """Every suppressed address (admin read). ``[]`` fail-soft."""
    try:
        with closing(_get_conn()) as conn:
            conn.row_factory = sqlite3.Row
            return [_row(r) for r in conn.execute(
                "SELECT * FROM email_suppression ORDER BY suppressed_at DESC"
            ).fetchall()]
    except Exception as exc:
        print(f"[Notifications] list_suppressed_emails failed: {exc}")
        return []


def bump_soft_bounce(email: str) -> int:
    """Increment the CONSECUTIVE soft-bounce counter and return the new count.
    ``0`` on bad input / store failure (a failure must not manufacture an
    alert).

    The row also stamps ``first_at`` — the start of the current streak — which
    :func:`soft_bounce_streak_start` reads. See that function for why the
    caller needs it.
    """
    norm = (email or "").strip().lower()
    if not norm:
        return 0
    now = _now()
    try:
        with closing(_get_conn()) as conn:
            conn.execute(
                "INSERT INTO soft_bounce_counts (email, count, last_at, first_at) "
                "VALUES (?,1,?,?) "
                "ON CONFLICT(email) DO UPDATE SET count = count + 1, "
                "last_at = excluded.last_at, "
                "first_at = COALESCE(soft_bounce_counts.first_at, excluded.first_at)",
                (norm, now, now))
            conn.commit()
            r = conn.execute("SELECT count FROM soft_bounce_counts WHERE email = ?",
                             (norm,)).fetchone()
            return int(r[0]) if r else 0
    except Exception as exc:
        print(f"[Notifications] bump_soft_bounce failed: {exc}")
        return 0


def soft_bounce_streak_start(email: str) -> Optional[str]:
    """When the address's CURRENT run of consecutive soft bounces began.

    This is the identity of the streak, and it exists so the caller can raise
    exactly one alert per streak: an alert deduped on the address alone could
    never fire again after the first streak, and one deduped on the count fires
    again on every later bounce in the same streak. ``None`` when the address
    has no streak / on a store failure.
    """
    norm = (email or "").strip().lower()
    if not norm:
        return None
    try:
        with closing(_get_conn()) as conn:
            r = conn.execute(
                "SELECT first_at FROM soft_bounce_counts WHERE email = ?",
                (norm,)).fetchone()
            return r[0] if r else None
    except Exception as exc:
        print(f"[Notifications] soft_bounce_streak_start failed: {exc}")
        return None


def reset_soft_bounce(email: str) -> bool:
    """Reset the counter — called on a successful delivery, which is what
    makes the count CONSECUTIVE rather than lifetime."""
    norm = (email or "").strip().lower()
    if not norm:
        return False
    try:
        with closing(_get_conn()) as conn:
            conn.execute("DELETE FROM soft_bounce_counts WHERE email = ?", (norm,))
            conn.commit()
        return True
    except Exception as exc:
        print(f"[Notifications] reset_soft_bounce failed: {exc}")
        return False


def soft_bounce_count(email: str) -> int:
    """The current consecutive soft-bounce count for an address (``0`` when
    none / fail-soft)."""
    norm = (email or "").strip().lower()
    if not norm:
        return 0
    try:
        with closing(_get_conn()) as conn:
            r = conn.execute("SELECT count FROM soft_bounce_counts WHERE email = ?",
                             (norm,)).fetchone()
            return int(r[0]) if r else 0
    except Exception as exc:
        print(f"[Notifications] soft_bounce_count failed: {exc}")
        return 0
