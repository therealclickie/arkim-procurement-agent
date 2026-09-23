"""Badge integrity — the extractor may LOWER a match badge, never RAISE one.

Arc 5 / ruling R2 (evaluation finding F-11). In the evaluation's S2 run, three
Tier-3 rows were badged ``isExactMatch: true`` / ``pnMatchLevel: "exact"`` for a
``6205-2RS C3`` request while carrying ``foundPartNumber: "6205-2RS"`` — no C3 — on a
bare-domain URL with no listing page behind it
(``eval/e2e-flags-on:eval/e2e/evidence/s2_step4_candidate_analysis.json``, rows for
Rodavictoria USA, Intech Bearing Inc. and BDS Bearing). The badge came straight off
the LLM extractor's ``pn_match_status``; the deterministic classifier
(``sourcing_archieved.scoring._classify_pn_match``), which scores that pair ``none``,
was never consulted on the badge path.

This module is the gate every badge now passes through. Three rules, in order:

1. **The deterministic classifier is the ceiling.** Whatever the extractor claims,
   the badge can be no stronger than what ``_classify_pn_match`` independently
   agrees to on ``(searched_pn, found_pn, snippet, manufacturer)``.
2. **The extractor is advisory and may only downgrade.** Its ``pn_match_status``
   lowers the badge when it is weaker than the classifier's verdict; it never lifts
   one.
3. **No listing, no exact.** A row whose URL is empty or a bare domain has nothing a
   buyer could open to check the part number, so it can never carry an exact-grade
   badge however well the strings match.

Every verdict carries the classifier's reason, so the badge is explainable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse

#: Badge strength, weakest first. "Exact-grade" is the tail the UI renders as an
#: exact replacement (``options-screen.tsx``: ``isExactMatch || pnMatchLevel in
#: {exact, normalized}``), which is why the bare-domain cap has to sit below BOTH.
LEVEL_ORDER: tuple[str, ...] = ("none", "substring", "stem", "normalized", "exact")

#: The levels the UI presents as "this IS the requested part".
EXACT_GRADE: frozenset[str] = frozenset({"exact", "normalized"})

#: The strongest badge a row with no resolvable listing URL may carry.
_NO_LISTING_CAP = "stem"

_REASONS: dict[str, str] = {
    "exact":      "the listing's part number is the requested part number",
    "normalized": "the listing's part number matches once separator notation is normalised",
    "stem":       "the listing's part number shares the requested part's model family",
    "substring":  "the requested part number appears only in the listing snippet",
    "none":       "the listing's part number does not match the requested part number",
}

_NO_PN_REASON = "no part number was requested, so no part-number match can be claimed"
_NO_FOUND_PN_REASON = "the listing shows no part number to compare"
_NO_LISTING_REASON = ("no resolvable listing URL (bare domain) — the part number "
                      "cannot be verified on a page")
_DOWNGRADE_REASON = "the extractor reports a weaker match than the part numbers alone suggest"


@dataclass(frozen=True)
class BadgeVerdict:
    """The gated badge for one candidate row."""

    level: str
    is_exact: bool
    reason: str
    #: What the deterministic classifier said on its own, before the caps applied.
    classifier_level: str = "none"


def _rank(level: Optional[str]) -> int:
    try:
        return LEVEL_ORDER.index(level or "none")
    except ValueError:
        return 0


def has_resolvable_listing(url: Optional[str]) -> bool:
    """True iff ``url`` points at something more specific than a bare domain.

    A path, a query or a fragment all count as "there is a page to check". The S2
    rows that provoked F-11 (``https://rodavictoriausa.com``, ``https://vxb.com/``,
    ``""``) all fail this.
    """
    if not url or not str(url).strip():
        return False
    try:
        parsed = urlparse(str(url).strip())
    except (ValueError, TypeError):
        return False
    if not parsed.netloc and not parsed.path:
        return False
    path = (parsed.path or "").strip("/")
    return bool(path or parsed.query or parsed.fragment)


def classify(searched_pn: Optional[str], found_pn: Optional[str],
             snippet: Optional[str], manufacturer: Optional[str]) -> tuple[str, str]:
    """The deterministic verdict and its reason, independent of any extractor claim."""
    searched = (searched_pn or "").strip()
    if not searched:
        return "none", _NO_PN_REASON

    from utils.sourcing_archieved.scoring import _classify_pn_match
    try:
        level = _classify_pn_match(searched, found_pn, snippet or "", manufacturer)
    except Exception as exc:  # a classifier error must not license a badge
        print(f"[BadgeIntegrity] classifier failed for {searched!r}/{found_pn!r}: {exc}")
        return "none", "the part number could not be classified"

    if level == "none" and not (found_pn or "").strip():
        return level, _NO_FOUND_PN_REASON
    return level, _REASONS.get(level, _REASONS["none"])


def resolve(opt: dict[str, Any], *, advisory_level: str,
            searched_pn: Optional[str], manufacturer: Optional[str],
            claims_exact: bool) -> BadgeVerdict:
    """Gate one candidate's badge.

    ``advisory_level`` is the extractor-derived level the caller would have shown
    (tier 1: "found a part number"; tiers 2/3: the mapped ``pn_match_status``).
    ``claims_exact`` is the extractor's own "Exact OEM" assertion. Neither can raise
    the badge above what ``classify`` grants.
    """
    found_pn = opt.get("found_part_number") or opt.get("foundPartNumber")
    snippet = opt.get("snippet") or opt.get("description") or ""
    url = opt.get("source_url") or opt.get("url")

    classifier_level, reason = classify(searched_pn, found_pn, snippet, manufacturer)

    level = classifier_level
    if _rank(advisory_level) < _rank(level):      # rule 2 — downgrade only
        level = advisory_level or "none"
        reason = _DOWNGRADE_REASON

    # Rule 3. `level` is already min(classifier, advisory), so an exact-grade level
    # here always outranks the cap — capping is always a downgrade, never a lift.
    if level in EXACT_GRADE and not has_resolvable_listing(url):
        level = _NO_LISTING_CAP
        reason = _NO_LISTING_REASON

    is_exact = bool(claims_exact) and level == "exact"
    return BadgeVerdict(level=level, is_exact=is_exact, reason=reason,
                        classifier_level=classifier_level)
