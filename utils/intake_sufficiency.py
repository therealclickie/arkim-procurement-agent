"""Intake sufficiency — a request may not reach sourcing without its discriminators.

Arc 5 / ruling R4 (evaluation finding F-15). In the evaluation's S3 run, a request
whose entire captured spec was

    {"category": "Part", "description": "Replacement pressure gauge for CIP skid",
     "detected_type": "pressure gauge", "use_case": "CIP skid pressure measurement
     replacement", "manufacturer_confidence": 0.0, "part_id_confidence": 35.0, ...}

— no manufacturer, no model, no part number, no range, no connection — was confirmed
with a **200** and went straight to sourcing, returning priced Tier-2/3 results for a
part that was never specified
(``eval/e2e-flags-on:eval/e2e/evidence/s3_step2_confirm_attempt.json``; the verify
pass measured ``family_disambig_block`` returning ``None`` on those specs, so nothing
stopped it).

The floor, per R4: required fields come from the part-type registry where it defines
them — that is the pre-existing ``family_disambig_block``, which stays exactly as it
is and runs first. Where the registry does not define them, the minimum is a
**manufacturer plus a model, or a manufacturer part number**.

The override is deliberate and leaves a trace: ``source_anyway=true`` records an
acknowledgement on the run, marks it ``spec_incomplete``, and every result in such a
run carries a banner and can never be badged exact (see ``badge_integrity``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

#: Spec values that mean "not established". Mirrors ``intake_agent._NULL_VALUES``
#: so the floor agrees with the rest of intake about what counts as absent.
from utils.procurement_agent.agents.intake_agent import _NULL_VALUES

#: The spec key marking a run that reached sourcing on an explicit override.
SPEC_INCOMPLETE = "spec_incomplete"
#: The spec key holding the recorded acknowledgement.
OVERRIDE_ACK = "spec_incomplete_ack"

#: The buyer-facing banner every result in an overridden run carries.
BANNER = ("These results have NOT been checked against your requirement — the request "
          "was sourced without a manufacturer and model or a manufacturer part number.")

_REASON = "identity_insufficient"
_MESSAGE = ("This request has no manufacturer and model, and no manufacturer part "
            "number — there is nothing to match a supplier's listing against. Add "
            "them in the chat, or source anyway and review the results yourself.")


@dataclass(frozen=True)
class SufficiencyBlock:
    """Why a request may not start sourcing, in a shape the intake card can render."""

    reason: str
    message: str
    missing_fields: tuple[str, ...]
    missing_labels: tuple[str, ...]

    def as_detail(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "reason": self.reason,
            "missing_attrs": list(self.missing_fields),
            "missing_labels": list(self.missing_labels),
            "override": "source_anyway",
        }


def _present(specs: dict[str, Any], key: str) -> bool:
    value = specs.get(key)
    if isinstance(value, str):
        value = value.strip()
    return value not in _NULL_VALUES


def identity_block(specs: Optional[dict[str, Any]]) -> Optional[SufficiencyBlock]:
    """The identity floor: manufacturer AND (model OR part number).

    Returns None when the request clears it — which is the S1 shape (Chesterton /
    155) and every clean-part-number request. Returns a block for the S3 shape (a
    described class and nothing else).
    """
    specs = specs or {}
    has_manufacturer = _present(specs, "manufacturer")
    has_model = _present(specs, "model")
    has_part_number = _present(specs, "part_number")

    if has_part_number and has_manufacturer:
        return None                       # a manufacturer part number identifies it
    if has_manufacturer and has_model:
        return None

    missing: list[str] = []
    labels: list[str] = []
    if not has_manufacturer:
        missing.append("manufacturer")
        labels.append("manufacturer")
    if not (has_model or has_part_number):
        missing.append("model")
        labels.append("model or part number")
    return SufficiencyBlock(reason=_REASON, message=_MESSAGE,
                            missing_fields=tuple(missing), missing_labels=tuple(labels))


def record_override(specs: dict[str, Any], block: SufficiencyBlock, *,
                    acknowledged_by: Optional[str] = None,
                    at: Optional[str] = None) -> dict[str, Any]:
    """Mark the specs ``spec_incomplete`` and record the acknowledgement on the run.

    Mutates and returns ``specs`` — the same idiom ``_commit_intake_to_sourcing``
    already uses for ``exact_only`` / ``family_open_commit``.
    """
    from datetime import datetime, timezone

    specs[SPEC_INCOMPLETE] = True
    specs[OVERRIDE_ACK] = {
        "acknowledged": True,
        "reason": block.reason,
        "missing_attrs": list(block.missing_fields),
        "acknowledged_by": acknowledged_by,
        "acknowledged_at": at or datetime.now(timezone.utc).isoformat(),
    }
    # Sourcing already reads this marker as "no part number to match against"; an
    # overridden run IS a spec-based source, so say so in the existing vocabulary.
    specs["spec_based_sourcing"] = True
    return specs


def is_spec_incomplete(specs: Optional[dict[str, Any]]) -> bool:
    """True iff this run reached sourcing on a recorded override."""
    return bool((specs or {}).get(SPEC_INCOMPLETE))
