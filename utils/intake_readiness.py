"""Intake readiness — the ONE decision on whether a request may start sourcing.

PH-01 (review round 2, findings 1-3). The chat's "Specs look complete — confirm to
start sourcing" and ``confirm_intake``'s refusals used to be separate checks: the
chat keyed off the intake agent's own sufficiency, while confirm ran the arc-5
identity floor (``intake_sufficiency.identity_block``) and the hygienic question set
(``hygienic_context.hygienic_block``). A model-only request was told "complete" by the
chat and refused 422 ``identity_insufficient`` by confirm.

:func:`assess` combines both gates on the same specs. ``confirm_intake`` refuses on
it and ``send_message`` replies from it, so the chat can say "complete" only when
confirm would proceed, and otherwise asks for exactly what confirm is missing.

The registry-driven family-variant guard (``family_disambig_block``) is NOT folded in:
it has its own chat ask and its own ``open_family`` exit, and runs before this.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from utils import hygienic_context, intake_sufficiency
from utils.hygienic_context import HygienicBlock
from utils.intake_sufficiency import SufficiencyBlock

#: The spec ledger key counting hygienic asks the chat has made. ``_``-prefixed, so
#: it is persisted but stripped from the RunDetail display like ``_intake_turns``.
HYGIENIC_ASKS_KEY = "_hygienic_asks"

#: The spec key holding the acknowledgement of a hygienic ``source_anyway``.
HYGIENIC_OVERRIDE_ACK = "hygienic_override_ack"

#: From this hygienic ask onward the chat names the way out. The hygienic ask is
#: outside the intake turn cap (``INTAKE_TURN_CAP``), so this is the loop's exit.
SOURCE_ANYWAY_FROM_ASK = 2

SOURCE_ANYWAY_HINT = ("If you can't answer that, choose **Source anyway** when you "
                      "confirm — the results will carry a banner saying they were not "
                      "checked against your requirement, and none will be marked an "
                      "exact match.")

#: The chat's reply when readiness says ready but the intake agent still has a
#: question (PH-01 round 3c, finding 3): the buyer is NOT blocked, so the reply says
#: they can find options now and the question is optional.
READY_NOW = "You have enough to find options now — confirm in the panel to start sourcing."
OPTIONAL_PREFIX = "Optional, if you know it:"

#: The requirement groups a run can have been sourced without (PH-01 round 3c,
#: finding 2), in banner order.
IDENTITY = "identity"
HYGIENIC = "hygienic"


@dataclass(frozen=True)
class Readiness:
    """Whether confirm may proceed, and if not, everything it is missing."""

    identity: Optional[SufficiencyBlock]
    hygienic: Optional[HygienicBlock]

    @property
    def ready(self) -> bool:
        return self.identity is None and self.hygienic is None

    @property
    def missing_attrs(self) -> tuple[str, ...]:
        return tuple((self.identity.missing_fields if self.identity else ())
                     + (self.hygienic.missing_fields if self.hygienic else ()))

    @property
    def missing_labels(self) -> tuple[str, ...]:
        return tuple((self.identity.missing_labels if self.identity else ())
                     + (self.hygienic.missing_labels if self.hygienic else ()))

    @property
    def first_block(self) -> Optional[SufficiencyBlock | HygienicBlock]:
        """The block confirm refuses with: identity first, then hygienic."""
        return self.identity or self.hygienic

    def refusal_detail(self) -> Optional[dict[str, Any]]:
        """The 422 detail: the first block's own detail (its reason and message are
        unchanged), plus every missing item across BOTH gates, so a buyer who fixes
        the identity is not surprised by a hygienic refusal on the next confirm."""
        block = self.first_block
        if block is None:
            return None
        detail = block.as_detail()
        detail["all_missing_attrs"] = list(self.missing_attrs)
        detail["all_missing_labels"] = list(self.missing_labels)
        return detail

    def ask(self, *, hygienic_ask_number: int = 0) -> str:
        """The chat's question — exactly the missing items, nothing else.

        With only the hygienic gate open this is the hygienic 422's own message (the
        chat and the refusal read the same). From the second hygienic ask onward it
        also names the Source anyway option.
        """
        parts: list[str] = []
        if self.identity is not None:
            parts.append(identity_question(self.identity))
        if self.hygienic is not None:
            parts.append(self.hygienic.message)
            if hygienic_ask_number >= SOURCE_ANYWAY_FROM_ASK:
                parts.append(SOURCE_ANYWAY_HINT)
        return " ".join(parts)


def identity_question(block: SufficiencyBlock) -> str:
    """The identity floor's ask, composed from the block's own missing labels."""
    labels = list(block.missing_labels)
    phrase = labels[0] if len(labels) == 1 else " and the ".join(labels)
    return (f"Before sourcing I need the {phrase} — without them there is nothing to "
            f"match a supplier's listing against.")


def assess(specs: Optional[dict[str, Any]]) -> Readiness:
    """Run both confirm gates on ``specs`` (the persisted asset specs)."""
    specs = specs or {}
    return Readiness(identity=intake_sufficiency.identity_block(specs),
                     hygienic=hygienic_context.hygienic_block(specs))


def record_hygienic_override(specs: dict[str, Any], block: HygienicBlock, *,
                             at: Optional[str] = None) -> dict[str, Any]:
    """Record a hygienic ``source_anyway`` on the run. Mutates and returns ``specs``.

    Deliberately NOT ``intake_sufficiency.record_override``: that marks the run
    ``spec_incomplete`` and its banner says the manufacturer/model was missing, which
    is false for a request that cleared the identity floor. The marking comes from
    :func:`unverified_requirements`, which reads this acknowledgement.
    """
    from datetime import datetime, timezone

    specs[HYGIENIC_OVERRIDE_ACK] = {
        "acknowledged": True,
        "reason": block.reason,
        "missing_attrs": list(block.missing_fields),
        # The banner names these (unverified_banner), so record them as asked.
        "missing_labels": list(block.missing_labels),
        "acknowledged_at": at or datetime.now(timezone.utc).isoformat(),
    }
    return specs


def chat_ask(readiness: Readiness, specs: dict[str, Any]) -> str:
    """The chat's reply for a not-ready request, counting hygienic asks on ``specs``.

    Mutates ``specs`` (the counter is persisted with them) and returns
    :meth:`Readiness.ask` for this ask's number.
    """
    asks = int(specs.get(HYGIENIC_ASKS_KEY) or 0)
    if readiness.hygienic is not None:
        asks += 1
        specs[HYGIENIC_ASKS_KEY] = asks
    return readiness.ask(hygienic_ask_number=asks)


def ready_reply(question: Optional[str]) -> str:
    """The chat's reply when readiness says ready — any question is optional."""
    question = (question or "").strip()
    if not question:
        return READY_NOW
    return f"{READY_NOW} {OPTIONAL_PREFIX} {question}"


# ---------------------------------------------------------------------------
# Sourced with unmet requirements — ONE marking (PH-01 round 3c, finding 2)
# ---------------------------------------------------------------------------

def unverified_requirements(specs: Optional[dict[str, Any]]) -> tuple[str, ...]:
    """Which requirement groups this run was sourced without: ``identity``,
    ``hygienic``, both, or neither.

    Derived from the acknowledgement records confirm writes on ``source_anyway``
    (``spec_incomplete`` for identity, ``hygienic_override_ack`` for hygienic). The
    results banner AND the exact-badge cap both read this — nothing else decides
    whether a run's results are marked unchecked.
    """
    specs = specs or {}
    groups: list[str] = []
    if intake_sufficiency.is_spec_incomplete(specs):
        groups.append(IDENTITY)
    ack = specs.get(HYGIENIC_OVERRIDE_ACK)
    if isinstance(ack, dict) and ack.get("acknowledged"):
        groups.append(HYGIENIC)
    return tuple(groups)


def _hygienic_labels(specs: dict[str, Any]) -> list[str]:
    """The hygienic items the acknowledgement recorded as unconfirmed, as labels."""
    ack = specs.get(HYGIENIC_OVERRIDE_ACK) or {}
    labels = [str(l) for l in (ack.get("missing_labels") or []) if l]
    if labels:
        return labels
    # An acknowledgement recorded before labels were stored: map its attrs.
    return [hygienic_context.FIELD_LABELS.get(a, str(a))
            for a in (ack.get("missing_attrs") or []) if a]


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


_UNCHECKED = "These results have NOT been checked against your requirement"


def _hygienic_clause(specs: dict[str, Any]) -> str:
    labels = _hygienic_labels(specs)
    if not labels:
        return "without confirming its hygienic fitment"
    return f"without confirming its {_join(labels)}"


def unverified_banner_lines(specs: Optional[dict[str, Any]]) -> tuple[str, ...]:
    """The results banner naming what was not checked; empty for a checked run.

    - identity only: ``(intake_sufficiency.BANNER,)`` — arc 5's text, byte-identical;
    - hygienic only: one line naming the unconfirmed hygienic items;
    - both: arc 5's line unchanged, then a line naming the hygienic items. (Kept as
      two lines, not one rewritten sentence, so arc 5's line reads the same on every
      identity override — its S3 fixture is a hygienic-context gauge, i.e. a both
      run.)
    """
    specs = specs or {}
    groups = unverified_requirements(specs)
    lines: list[str] = []
    if IDENTITY in groups:
        lines.append(intake_sufficiency.BANNER)
    if HYGIENIC in groups:
        if lines:
            lines.append(f"It was also sourced {_hygienic_clause(specs)}.")
        else:
            lines.append(f"{_UNCHECKED} — the request was sourced "
                         f"{_hygienic_clause(specs)}.")
    return tuple(lines)


def unverified_badge_reason(specs: Optional[dict[str, Any]]) -> Optional[str]:
    """Why no row in this run may be badged exact, or None for a checked run.

    Any identity override: exactly ``badge_integrity.SPEC_INCOMPLETE_REASON``
    (unchanged — "nothing was checked against a requirement" covers both groups).
    Hygienic only: names the unconfirmed hygienic items.
    """
    from utils import badge_integrity

    specs = specs or {}
    groups = unverified_requirements(specs)
    if IDENTITY in groups:
        return badge_integrity.SPEC_INCOMPLETE_REASON
    if HYGIENIC in groups:
        return (f"the request was sourced {_hygienic_clause(specs)} — its fitment was "
                f"not checked against a requirement")
    return None
