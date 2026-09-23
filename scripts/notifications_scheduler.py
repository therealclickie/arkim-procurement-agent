"""Arc 4 — the notifications scheduler CLI (D6 / GATE RULINGS).

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT
=============================================
``utils.notifications.run_escalations`` is a **plain function over the current
state of the store**. This file is the only way it runs: an operator's cron
entry, an ECS scheduled task, or a human at a shell.

There is **no in-process timer, no background thread, no APScheduler and no
Celery** anywhere in arc 4 — the brief's GATE RULINGS forbid all four, and the
reason is worth restating: a timer inside the API process would fire on every
worker (so the "at most one reminder" guarantee would depend on how many
workers happen to be running), would silently stop when the process restarted,
and would be untestable without sleeping. A plain function called from outside
takes ``now`` as an argument, is idempotent when run twice with the same
``now``, and is table-testable with no clock at all.

FLAG POSTURE
------------
``NOTIFICATIONS_V1`` off ⇒ the function no-ops and returns zeroes. Running this
script on a box where the flag is off is harmless and says so.

USAGE
-----
    uv run python scripts/notifications_scheduler.py escalations
    uv run python scripts/notifications_scheduler.py escalations --now 2026-09-21T09:00:00+00:00
    uv run python scripts/notifications_scheduler.py escalations --json
    uv run python scripts/notifications_scheduler.py coalesce
    uv run python scripts/notifications_scheduler.py digest

    # Cron (UTC). Hourly for the ladder — its resolution is hours, so hourly is
    # plenty and a missed hour self-heals on the next run. Once a day for the
    # digest, because "daily" is the promise the member opted into:
    #   */5 * * * * cd /srv/arkim && uv run python scripts/notifications_scheduler.py coalesce
    #   0 * * * * cd /srv/arkim && uv run python scripts/notifications_scheduler.py escalations
    #   0 7 * * * cd /srv/arkim && uv run python scripts/notifications_scheduler.py digest

``--now`` exists for replay and for a controlled operational catch-up; it is
parsed as ISO-8601 and a naive value is read as UTC (the store's convention).

Exit code is 0 whenever the run completed, including a run that did nothing —
a scheduler entry that exits non-zero on "nothing to do" trains operators to
ignore its alerts.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Make the repo root importable so `utils.*` resolves when run via uv.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from utils import notifications  # noqa: E402 — after the sys.path fix


def parse_now(raw: Optional[str]) -> Optional[datetime]:
    """``--now`` → an aware UTC datetime, or ``None`` for "right now".

    A naive timestamp is read as UTC rather than as local time: every stored
    timestamp in this system is UTC, and quietly reinterpreting an operator's
    input in the box's timezone would shift the whole ladder by the offset.
    """
    if not raw:
        return None
    moment = datetime.fromisoformat(raw)
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None \
        else moment.astimezone(timezone.utc)


def cmd_escalations(args: argparse.Namespace) -> int:
    """D6's ladder: remind at ``ESCALATE_REMIND_HOURS``, concierge alert at
    ``ESCALATE_ALERT_HOURS``, where "seen" is viewed-in-portal OR clicked."""
    result = notifications.run_escalations(parse_now(args.now))
    _report("escalations", result, as_json=args.json)
    return 0


def cmd_coalesce(args: argparse.Namespace) -> int:
    """Arc 4b S2: send every RFQ_NEW whose coalescing window has closed.

    Run it OFTEN — every few minutes. This is the step that actually delivers
    RFQ mail now, so its cron entry is not optional: a box that runs the
    ladder but not this one will chase suppliers about requests it never sent
    them. The window (NOTIFY_COALESCE_MINUTES, default 15) sets the worst-case
    delay; the cron interval adds to it, so keep the interval well under the
    window.
    """
    result = notifications.run_coalesced_sends(parse_now(args.now))
    _report("coalesce", result, as_json=args.json)
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    """D7's DAILY_DIGEST: every deferred RFQ_NEW for a member, batched into one
    mail. Run it ONCE a day — running it twice a day is not incorrect (the
    second pass finds nothing deferred) but it is two mails a day, which is not
    what the member asked for."""
    result = notifications.run_daily_digest(parse_now(args.now))
    _report("digest", result, as_json=args.json)
    return 0


def _report(label: str, result: dict, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"command": label, **result}))
        return
    active = notifications.notifications_active()
    if not active:
        print(f"[{label}] NOTIFICATIONS_V1 is off — nothing to do.")
        return
    print(f"[{label}] " + " ".join(f"{k}={v}" for k, v in sorted(result.items())))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="notifications_scheduler",
        description="Arc 4 notification scheduler entry points (cron-driven; "
                    "no in-process timers).")
    sub = parser.add_subparsers(dest="command", required=True)

    esc = sub.add_parser("escalations",
                         help="run the D6 escalation ladder once")
    esc.add_argument("--now", default=None,
                     help="ISO-8601 instant to evaluate against (default: now, UTC)")
    esc.add_argument("--json", action="store_true",
                     help="emit the result as one JSON line")
    esc.set_defaults(func=cmd_escalations)

    coa = sub.add_parser("coalesce",
                         help="send RFQ_NEW batches whose window has closed")
    coa.add_argument("--now", default=None,
                     help="ISO-8601 instant to evaluate against (default: now, UTC)")
    coa.add_argument("--json", action="store_true",
                     help="emit the result as one JSON line")
    coa.set_defaults(func=cmd_coalesce)

    dig = sub.add_parser("digest",
                         help="send the D7 daily digest for DAILY_DIGEST members")
    dig.add_argument("--now", default=None,
                     help="ISO-8601 instant to evaluate against (default: now, UTC)")
    dig.add_argument("--json", action="store_true",
                     help="emit the result as one JSON line")
    dig.set_defaults(func=cmd_digest)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
