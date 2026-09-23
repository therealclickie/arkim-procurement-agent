"""Hygienic service — the questions a sanitary instrument or fitting must answer.

Arc 5 / ruling R5 (evaluation finding F-16). The evaluation's S3 request said "the
pressure gauge on the CIP skid", and the extractor's own reasoning said so too —
``"CIP applications typically require sanitary-grade gauges, but this cannot be
confirmed without more detail"`` — yet nothing acted on it, and the run sourced
ordinary industrial gauges
(``eval/e2e-flags-on:eval/e2e/evidence/s3_step2_confirm_attempt.json``).

R5, narrowly: when intake context indicates hygienic service, **instruments and
fittings** must answer process connection type and size, wetted material, and
hygienic certification (3-A / EHEDG / none) before confirm. This is a **question-set
addition only** — there is no hygienic equivalence logic here, and none belongs here.

The token set is a superset of the registry's existing ``_SANITARY_INFERENCE`` /
``_WASHDOWN_INFERENCE`` keys (``part_type_registry.py``), extended rather than
duplicated, exactly as the gate recommended.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

#: R5's trigger vocabulary. CIP/dairy/sanitary/food/washdown already drive the
#: registry's inference rules; SIP, beverage, pharma, 3-A, EHEDG and tri-clamp are
#: R5's additions.
HYGIENIC_TOKENS: tuple[str, ...] = (
    "cip", "sip", "sanitary", "washdown", "wash down", "wash-down",
    "food", "dairy", "beverage", "pharma", "pharmaceutical",
    "3-a", "3a-", "ehedg", "tri-clamp", "triclamp", "tri clamp", "hygienic",
)

#: Tokens that need a word boundary to avoid absurd substring hits ("cip" inside
#: "principal", "sip" inside "dissipate", "3a" inside a part number).
_WORD_BOUNDED = {"cip", "sip", "3-a", "3a-", "food", "dairy"}

#: The spec fields intake reads for context. ``description`` and ``use_case`` both
#: carried the CIP token in S3.
_CONTEXT_FIELDS: tuple[str, ...] = (
    "description", "use_case", "raw_text", "failure_mode", "duty_cycle",
    "confidence_reasoning", "material_spec", "detected_type",
)

#: The classes R5 scopes this to. Instruments and fittings only — not pumps, motors
#: or valves, whose own profiles already carry material questions.
_INSTRUMENT_TOKENS: tuple[str, ...] = (
    "gauge", "gage", "sensor", "transmitter", "transducer", "instrument",
    "switch", "probe", "thermocouple", "rtd", "flow meter", "flowmeter",
    "level", "pressure", "temperature",
)
_FITTING_TOKENS: tuple[str, ...] = (
    "fitting", "clamp", "ferrule", "gasket", "tee", "elbow", "reducer",
    "union", "adapter", "coupling", "hose", "tubing",
)

#: The required field set, in ask order. Names match the registry's existing
#: ``sensor_instrument.blocking_attrs`` where they already exist
#: (``process_connection``, ``wetted_material``); the two R5 adds are the
#: connection SIZE and the hygienic certification.
REQUIRED_FIELDS: tuple[str, ...] = (
    "process_connection",
    "process_connection_size",
    "wetted_material",
    "hygienic_certification",
)

FIELD_LABELS: dict[str, str] = {
    "process_connection":      "process connection type",
    "process_connection_size": "process connection size",
    "wetted_material":         "wetted material",
    "hygienic_certification":  "hygienic certification (3-A / EHEDG / none)",
}

#: Spec keys that already answer each required field, in preference order.
_FIELD_SOURCES: dict[str, tuple[str, ...]] = {
    "process_connection":      ("process_connection", "connection", "connection_type"),
    "process_connection_size": ("process_connection_size", "connection_size"),
    "wetted_material":         ("wetted_material", "material_spec", "wetted"),
    "hygienic_certification":  ("hygienic_certification", "certification", "hygienic_cert"),
}

_REASON = "hygienic_spec_incomplete"

from utils.procurement_agent.agents.intake_agent import _NULL_VALUES


@dataclass(frozen=True)
class HygienicBlock:
    """The hygienic questions still unanswered, in a shape the intake card renders."""

    reason: str
    message: str
    matched_tokens: tuple[str, ...]
    missing_fields: tuple[str, ...]
    missing_labels: tuple[str, ...]

    def as_detail(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "reason": self.reason,
            "hygienic_context": list(self.matched_tokens),
            "missing_attrs": list(self.missing_fields),
            "missing_labels": list(self.missing_labels),
            "override": "source_anyway",
        }


def _haystack(specs: dict[str, Any], extra_text: Optional[str] = None) -> str:
    parts = [str(specs.get(f) or "") for f in _CONTEXT_FIELDS]
    if extra_text:
        parts.append(str(extra_text))
    return " ".join(parts).lower()


def matched_tokens(specs: Optional[dict[str, Any]],
                   extra_text: Optional[str] = None) -> tuple[str, ...]:
    """The R5 tokens present in the request's context, in declaration order."""
    text = _haystack(specs or {}, extra_text)
    hits: list[str] = []
    for token in HYGIENIC_TOKENS:
        if token in _WORD_BOUNDED:
            if re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", text):
                hits.append(token)
        elif token in text:
            hits.append(token)
    return tuple(hits)


def is_hygienic(specs: Optional[dict[str, Any]],
                extra_text: Optional[str] = None) -> bool:
    """True iff intake context indicates hygienic service."""
    return bool(matched_tokens(specs, extra_text))


def is_instrument_or_fitting(specs: Optional[dict[str, Any]]) -> bool:
    """True iff the identified class is one R5 scopes these questions to."""
    specs = specs or {}
    kind = " ".join(str(specs.get(f) or "")
                    for f in ("detected_type", "_classified_type", "description")).lower()
    if any(t in kind for t in _FITTING_TOKENS):
        return True
    if (specs.get("_classified_type") or "") == "sensor_instrument":
        return True
    return any(t in kind for t in _INSTRUMENT_TOKENS)


def _answered(specs: dict[str, Any], field: str) -> bool:
    for key in _FIELD_SOURCES[field]:
        value = specs.get(key)
        if isinstance(value, str):
            value = value.strip()
        if value not in _NULL_VALUES:
            return True
    return False


def missing_fields(specs: Optional[dict[str, Any]]) -> tuple[str, ...]:
    """Which of R5's required fields this request has not answered, in ask order."""
    specs = specs or {}
    return tuple(f for f in REQUIRED_FIELDS if not _answered(specs, f))


def question(missing: Iterable[str]) -> str:
    """The ask, composed from the FIELD SET — never free text.

    Built by joining ``FIELD_LABELS`` for the missing fields, so the question can
    only ever name fields R5 defines.
    """
    labels = [FIELD_LABELS[f] for f in missing if f in FIELD_LABELS]
    if not labels:
        return ""
    if len(labels) == 1:
        phrase = labels[0]
    else:
        phrase = ", ".join(labels[:-1]) + " and " + labels[-1]
    return (f"This is hygienic service, so the part has to match the skid: "
            f"what {phrase}?")


def hygienic_block(specs: Optional[dict[str, Any]],
                   extra_text: Optional[str] = None) -> Optional[HygienicBlock]:
    """The R5 verdict: None when confirm may proceed, else the questions to ask.

    Fires only for an instrument or fitting in hygienic context with at least one
    required field unanswered.
    """
    specs = specs or {}
    tokens = matched_tokens(specs, extra_text)
    if not tokens:
        return None
    if not is_instrument_or_fitting(specs):
        return None
    missing = missing_fields(specs)
    if not missing:
        return None
    return HygienicBlock(
        reason=_REASON,
        message=question(missing),
        matched_tokens=tokens,
        missing_fields=missing,
        missing_labels=tuple(FIELD_LABELS[f] for f in missing),
    )
