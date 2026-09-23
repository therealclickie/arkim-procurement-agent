"""
utils/notification_metrics.py
Arc 4b T11 / S7 — measuring whether a notification kind is worth sending.

WHY THIS EXISTS
---------------
You cannot reason your way to the right notification volume in advance.
Opinion will not tell us whether RFQ_REMINDER earns its place in somebody's
inbox; behaviour will. So for each kind the funnel is recorded and a rate is
computed, and a kind whose rate sits below the floor over a rolling window is
FLAGGED FOR REVIEW — not silently switched off, because the measurement is
evidence for a decision, not the decision.

THE FUNNEL, AND WHAT EACH STEP MEANS
-------------------------------------
  sent       we handed it to the provider;
  delivered  the provider says it arrived (D4's Delivery event);
  viewed     somebody at that supplier opened the request IN THE PORTAL within
             one BUSINESS day of the send. Not an email open — Apple Mail
             Privacy Protection fires the pixel for everyone, so an open-based
             rate would measure Apple, not suppliers;
  quoted     a quote for that run arrived from that supplier after the send.

``viewed / sent`` is the actionability rate: the share of sends that produced
the action the notification was asking for.

TWO PROPERTIES THIS MODULE IS BUILT AROUND
-------------------------------------------
1. **``now`` is a parameter.** Nothing here reads the wall clock, so the same
   inputs give the same answer on any calendar date (criterion 10, reviewer
   R10). The AST test in ``test_notification_actionability.py`` enforces it.
2. **No data is NOT zero.** A kind that has never been sent reports
   ``"no data"``, never ``0%`` — a rate of zero out of zero would flag a kind
   for removal on the strength of no evidence at all, which is the same
   mistake in the opposite direction from over-alerting.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Optional

from utils import notifications_store as store

ENV_ACTIONABILITY_FLOOR = "NOTIFY_ACTIONABILITY_FLOOR"
DEFAULT_ACTIONABILITY_FLOOR = 0.20
DEFAULT_WINDOW_DAYS = 30

# The kinds worth measuring: the ones we PUSH. Auth mail and invites are
# requested by the recipient or their colleague, so an "actionability rate"
# over them would measure whether people sign in, not whether we should have
# written to them.
MEASURED_KINDS: tuple[str, ...] = (
    store.KIND_RFQ_NEW, store.KIND_RFQ_REMINDER, store.KIND_RFQ_DIGEST,
)

STATUS_NO_DATA = "no data"
STATUS_OK = "ok"
STATUS_BELOW_FLOOR = "below floor"


def actionability_floor() -> float:
    """S7's review threshold (default 0.20). Read LIVE; unparseable ⇒ the
    default, because a typo must not flag every kind for removal."""
    raw = (os.environ.get(ENV_ACTIONABILITY_FLOOR) or "").strip()
    if not raw:
        return DEFAULT_ACTIONABILITY_FLOOR
    try:
        return float(raw)
    except ValueError:
        print(f"[NotificationMetrics] {ENV_ACTIONABILITY_FLOOR}={raw!r} is not "
              f"a number — using {DEFAULT_ACTIONABILITY_FLOOR}")
        return DEFAULT_ACTIONABILITY_FLOOR


def _parse(value: Optional[str]) -> Optional[datetime]:
    """Tolerant ISO parse → aware UTC; ``None`` on blank/unparseable."""
    from datetime import timezone
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None \
        else dt.astimezone(timezone.utc)


def _viewed_in_time(notification: dict, sent_at: datetime) -> bool:
    """Did anyone at this supplier open the request in the portal within ONE
    BUSINESS day of the send?

    "Within one business day" rather than "ever": a view three weeks later is
    a real view and a failed notification — the mail did not get anybody to
    act, which is precisely what the rate is trying to see.
    """
    run_id = notification.get("run_id")
    if not run_id:
        return False
    from utils import business_hours
    tz_name = business_hours.account_timezone(notification.get("account_id"))
    deadline = business_hours.business_hours_after(sent_at, 8.0, tz_name)
    for view in store.list_rfq_views(
            run_id, supplier_domain=notification.get("supplier_domain")):
        first = _parse(view.get("first_viewed_at"))
        if first is not None and sent_at <= first <= deadline:
            return True
    return False


def _quoted_after(notification: dict, sent_at: datetime) -> bool:
    """Did a quote for this run arrive from this supplier after the send?

    Fail-soft ``False``: with ``QUOTE_SUBMIT_V1`` off there are no quotes to
    find, and a measurement that cannot read its input must report an absence,
    never invent a success.
    """
    run_id = notification.get("run_id")
    domain = notification.get("supplier_domain")
    if not run_id or not domain:
        return False
    try:
        from utils import quote_store
        quotes = quote_store.get_quotes(run_id=run_id, supplier_domain=domain)
    except Exception as exc:
        print(f"[NotificationMetrics] quote read failed: {exc}")
        return False
    for quote in quotes or []:
        submitted = _parse(quote.get("submitted_at"))
        if submitted is not None and submitted >= sent_at:
            return True
    return False


def actionability(now: datetime, *, days: int = DEFAULT_WINDOW_DAYS,
                  floor: Optional[float] = None) -> dict:
    """The rolling-window funnel and rate for every measured kind (S7).

    ``now`` is a PARAMETER and the window is ``[now - days, now]``; no clock is
    read here or below. Returns::

        {"window_days": 30, "floor": 0.2, "as_of": "...",
         "kinds": [{"kind", "sent", "delivered", "viewed", "quoted",
                    "actionability_rate", "quote_rate", "status", "flagged"}]}

    ``actionability_rate`` and ``quote_rate`` are ``None`` — not ``0`` — for a
    kind with no sends in the window, and its status is ``"no data"``.
    """
    limit = floor if floor is not None else actionability_floor()
    since = now - timedelta(days=max(days, 0))
    rows: list[dict[str, Any]] = []
    try:
        everything = store.list_notifications()
    except Exception as exc:
        print(f"[NotificationMetrics] store read failed: {exc}")
        everything = []

    for kind in MEASURED_KINDS:
        sent = delivered = viewed = quoted = 0
        for notification in everything:
            if notification.get("kind") != kind:
                continue
            sent_at = _parse(notification.get("sent_at"))
            if sent_at is None or not (since <= sent_at <= now):
                continue
            sent += 1
            if notification.get("delivered_at"):
                delivered += 1
            if _viewed_in_time(notification, sent_at):
                viewed += 1
            if _quoted_after(notification, sent_at):
                quoted += 1
        rows.append(_summarise(kind, sent, delivered, viewed, quoted, limit))
    return {"window_days": days, "floor": limit, "as_of": now.isoformat(),
            "kinds": rows}


def _summarise(kind: str, sent: int, delivered: int, viewed: int, quoted: int,
               floor: float) -> dict:
    """One kind's row. Pure arithmetic — no store, no clock — so the "no data
    is not zero" rule is stated once and table-tested."""
    if sent == 0:
        return {"kind": kind, "sent": 0, "delivered": 0, "viewed": 0,
                "quoted": 0, "actionability_rate": None, "quote_rate": None,
                "status": STATUS_NO_DATA, "flagged": False}
    rate = viewed / sent
    below = rate < floor
    return {"kind": kind, "sent": sent, "delivered": delivered,
            "viewed": viewed, "quoted": quoted,
            "actionability_rate": round(rate, 4),
            "quote_rate": round(quoted / sent, 4),
            "status": STATUS_BELOW_FLOOR if below else STATUS_OK,
            "flagged": below}
