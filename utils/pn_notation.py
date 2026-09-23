"""Part-number NOTATION: separators, bearing clearance codes and seal designations.

Arc 5 / ruling R3 (evaluation finding F-12). For a request of SKF ``6205-2RS C3``,
the verify pass observed the deterministic classifier scoring three listings that DO
carry the right clearance as ``none`` — JSB Great Bearings ``6205-2RS-C3``, Motion
Industries ``6205-2RS-C3`` and 123Bearing ``6205-2RSH-C3-SKF`` — while listings that
carry NO clearance at all (EIS ``6205-2RS``, PGN ``6205-2RS``) were scored the very
same ``none``, so the two cases were indistinguishable
(``eval/e2e-flags-on:eval/e2e/evidence/verify/s2_step4_candidate_analysis.json``).

R3 splits them:

* **Separator/clearance notation is normalised.** ``C3``, ``/C3``, ``-C3`` and
  `` C3`` compare equal. (``_classify_pn_match``'s ``normalize_part_number``
  already delivers this; these verdicts preserve it.)
* **A clearance difference is a** ``mismatch`` **with the reason stated** — requested
  and absent ("C3 requested; listing is CN/unspecified"), or plainly different.
* **A seal-designation difference within one family is** ``needs_verification`` —
  SKF ``2RSH`` or ``2RS1`` against a generic ``2RS`` is never ``exact`` and never
  ``none``/incompatible purely on notation.

Cross-maker seal equivalence is NOT decided here — that belongs to the equivalence
engine (explicitly out of scope for this arc).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

#: The verdicts this module returns. ``unrelated`` means "notation has nothing to say
#: about this pair" — the caller falls through to the ordinary PN classifier.
EQUAL = "equal"
MISMATCH = "mismatch"
NEEDS_VERIFICATION = "needs_verification"
UNRELATED = "unrelated"

#: Radial internal clearance codes. CN is the normal (unmarked) class, which is why
#: an absent clearance on a C3 request reads as "CN/unspecified" rather than "unknown".
_CLEARANCE_RE = re.compile(r"^C(N|[0-5])$")

#: Seal / shield designations: an optional "2" (both sides), the family (RS contact
#: rubber, RZ low-friction, Z shield), then a maker-specific designation suffix.
_SEAL_RE = re.compile(r"^(2?)(RS|RZ|Z)([A-Z0-9]*)$")

_UNSPECIFIED_CLEARANCE = "CN/unspecified"


@dataclass(frozen=True)
class NotationVerdict:
    """What notation alone can say about one (requested, found) part-number pair."""

    verdict: str
    reason: str = ""


@dataclass(frozen=True)
class _Parsed:
    base: str
    clearance: Optional[str]
    seal_family: Optional[str]
    seal_designation: Optional[str]
    other: tuple[str, ...]


def _tokens(pn: str) -> list[str]:
    """Split on ANY non-alphanumeric run — this is where separator notation dies.

    ``6205-2RS/C3``, ``6205-2RS-C3``, ``6205 2RS C3`` all yield the same tokens.
    """
    return [t for t in re.split(r"[^A-Za-z0-9]+", (pn or "").upper()) if t]


def _maker_tokens(manufacturer: Optional[str]) -> set[str]:
    return {t for t in _tokens(manufacturer or "") if t}


def parse(pn: str, manufacturer: Optional[str] = None) -> Optional[_Parsed]:
    """Split a part number into base / clearance / seal / leftover designations.

    Returns None when there is nothing to parse.
    """
    toks = _tokens(pn)
    if not toks:
        return None
    makers = _maker_tokens(manufacturer)
    base, rest = toks[0], toks[1:]

    clearance: Optional[str] = None
    family: Optional[str] = None
    designation: Optional[str] = None
    other: list[str] = []

    for tok in rest:
        if tok in makers:                       # "…-C3-SKF": the maker, not a designation
            continue
        if clearance is None and _CLEARANCE_RE.match(tok):
            clearance = "CN" if tok == "CN" else tok
            continue
        seal = _SEAL_RE.match("2Z" if tok == "ZZ" else tok)
        if family is None and seal:
            both, kind, suffix = seal.groups()
            family = f"{both or ''}{kind}"
            designation = tok
            if suffix:
                # The suffix is the maker's own variant marking (2RSH, 2RS1, 2RSJEM).
                family = f"{both or ''}{kind}"
            continue
        other.append(tok)

    return _Parsed(base=base, clearance=clearance, seal_family=family,
                   seal_designation=designation, other=tuple(other))


def _has_notation(p: Optional[_Parsed]) -> bool:
    return bool(p and (p.clearance or p.seal_family))


def classify(searched_pn: Optional[str], found_pn: Optional[str],
             manufacturer: Optional[str] = None) -> NotationVerdict:
    """The notation verdict for a (requested, found) pair.

    ``unrelated`` whenever the two part numbers do not share a base, or neither side
    carries a clearance or seal designation — notation then has nothing to add and the
    ordinary classifier decides.
    """
    req = parse(searched_pn or "", manufacturer)
    got = parse(found_pn or "", manufacturer)
    if not req or not got:
        return NotationVerdict(UNRELATED)
    if req.base != got.base:
        return NotationVerdict(UNRELATED)
    if not (_has_notation(req) or _has_notation(got)):
        return NotationVerdict(UNRELATED)

    # Clearance first: it is a fit-affecting difference, not a labelling one.
    if req.clearance and req.clearance != (got.clearance or None):
        got_label = got.clearance or _UNSPECIFIED_CLEARANCE
        return NotationVerdict(
            MISMATCH, f"{req.clearance} requested; listing is {got_label}")
    if got.clearance and not req.clearance:
        return NotationVerdict(
            NEEDS_VERIFICATION,
            f"listing is {got.clearance}; no internal clearance was requested")

    # Seal designation within one family: labelling, so never a mismatch — but never
    # an exact claim either. Across families notation says nothing (cross-maker seal
    # equivalence belongs to the equivalence engine).
    if req.seal_family and got.seal_family:
        if req.seal_family != got.seal_family:
            return NotationVerdict(UNRELATED)
        if req.seal_designation != got.seal_designation:
            return NotationVerdict(
                NEEDS_VERIFICATION,
                f"{req.seal_designation} requested; listing is {got.seal_designation} "
                f"- same {req.seal_family} seal family, different designation")
    elif req.seal_family or got.seal_family:
        want = req.seal_designation or "no seal designation"
        have = got.seal_designation or "no seal designation"
        return NotationVerdict(
            NEEDS_VERIFICATION, f"{want} requested; listing shows {have}")

    # Same base, same clearance, same seal — but the listing carries an extra
    # designation (a grease/cage/tolerance code such as HT51). Honest, not exact.
    extra = tuple(t for t in got.other if t not in req.other)
    if extra:
        return NotationVerdict(
            NEEDS_VERIFICATION,
            f"listing carries the additional designation {' '.join(extra)}")
    missing = tuple(t for t in req.other if t not in got.other)
    if missing:
        return NotationVerdict(
            NEEDS_VERIFICATION,
            f"{' '.join(missing)} requested; the listing does not show it")

    return NotationVerdict(EQUAL, "clearance and seal designation agree")
