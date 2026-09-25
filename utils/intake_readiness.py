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
                      "confirm — the results will be marked as not checked against "
                      "your requirement.")


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
    is false for a request that cleared the identity floor.
    """
    from datetime import datetime, timezone

    specs[HYGIENIC_OVERRIDE_ACK] = {
        "acknowledged": True,
        "reason": block.reason,
        "missing_attrs": list(block.missing_fields),
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
