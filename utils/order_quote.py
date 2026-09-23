"""Quote-priced ordering — the accepted structured quote IS the order's price.

Arc 5 / ruling R1 (evaluation finding F-07). The evaluation's S1 run ended with an
order at ``unit_price = NULL`` / ``status = "draft"`` while an ``active`` structured
quote for the very same ``(run_id, supplier_domain)`` sat at $189.00 / "2 days" in
``quote_store`` — the order path never consulted it
(``eval/e2e/evidence/verify/verify_offline_checks.json`` → ``F-07``,
``order_path_references_quote_store: false``).

This module is the single place that answers, for one order about to be created:
*is there an accepted quote for this candidate, and may the order proceed?*

The three answers, and only these three:

* **priced** — an effectively-active quote exists. Its price, currency, quantity and
  lead time become the order's, and its id is recorded on the order. The candidate's
  listing price is NEVER substituted for it.
* **refused** — a quote exists for this candidate but is expired / withdrawn /
  superseded / belongs to another run. Execution stops with an explicit,
  buyer-visible reason. It does NOT fall back to the listing price.
* **absent** — no quote was ever submitted for this candidate. The pre-existing
  listing-price / price_db path runs unchanged.

Gated on ``QUOTE_SUBMIT_V1`` (``quote_store.quote_submit_active``): with the flag off
this module is a no-op returning ``absent``, so flags-off behaviour — and every test
that does not isolate ``quote_store`` — is byte-identical to today.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

# quote_store statuses that mean "there was a quote, but it cannot price this order".
# Each maps to the buyer-visible reason execution refuses with.
_REFUSAL_REASONS: dict[str, str] = {
    "expired":     "the supplier's quote has expired",
    "withdrawn":   "the supplier withdrew their quote",
    "superseded":  "the supplier's quote was superseded by a newer one that is not active",
    "review":      "the supplier's quote is still under review and has not been accepted",
    "rejected":    "the supplier's quote was rejected",
}
_UNKNOWN_STATUS_REASON = "the supplier's quote is not active"

#: Reason used when the quote store cannot be read. Refusing — rather than falling
#: back to the listing price — is the honest answer: a failed read cannot PROVE the
#: absence of a quote, and R1 forbids claiming more than we can prove.
UNVERIFIABLE_REASON = "the supplier's quotes for this request could not be verified"


@dataclass(frozen=True)
class QuoteResolution:
    """The verdict for one candidate about to become an order."""

    quote: Optional[dict[str, Any]] = None
    refusal: Optional[str] = None

    @property
    def priced(self) -> bool:
        """True iff an accepted quote must price this order."""
        return self.quote is not None

    @property
    def refused(self) -> bool:
        """True iff execution must stop rather than fall back to a listing price."""
        return self.refusal is not None


ABSENT = QuoteResolution()


def _refusal(status: str, *, vendor: Optional[str]) -> QuoteResolution:
    reason = _REFUSAL_REASONS.get(status, _UNKNOWN_STATUS_REASON)
    who = vendor or "the supplier"
    return QuoteResolution(refusal=(
        f"Cannot place this order: {reason.replace('the supplier', who, 1)}. "
        f"Request a new quote from {who} before ordering."
    ))


def resolve_for_order(run_id: Optional[str], source_url: Optional[str], *,
                      supplier_domain: Optional[str] = None,
                      vendor_name: Optional[str] = None) -> QuoteResolution:
    """Resolve the accepted quote (if any) that must price this candidate's order.

    ``source_url`` is normalised to a domain the same way the buyer card's
    ``_resolve_quote`` does, so the order agrees with what the buyer was shown.
    Never raises.
    """
    from utils import quote_store

    if not quote_store.quote_submit_active():
        return ABSENT
    if not run_id:
        return ABSENT

    domain = supplier_domain
    if not domain and source_url:
        try:
            from utils.supplier_registry import _normalize_domain
            domain = _normalize_domain(source_url)
        except Exception:
            domain = None
    if not domain:
        return ABSENT

    try:
        rows = quote_store.get_quotes(run_id=run_id, supplier_domain=domain)
    except Exception as exc:  # a read we cannot complete is not a proof of absence
        print(f"[OrderQuote] quote lookup failed for {run_id!r}/{domain!r}: {exc}")
        return QuoteResolution(refusal=(
            f"Cannot place this order: {UNVERIFIABLE_REASON}."
        ))

    if not rows:
        return ABSENT

    newest = rows[0]                       # get_quotes orders newest-first
    status = newest.get("effective_status") or newest.get("status")
    if status != quote_store.STATUS_ACTIVE:
        return _refusal(str(status), vendor=vendor_name or newest.get("vendor_name"))
    # A quote that no longer matches the run it was filtered by (defensive; the
    # store filters on run_id, so this can only fire on a corrupted row).
    if newest.get("run_id") and newest.get("run_id") != run_id:
        return QuoteResolution(refusal=(
            "Cannot place this order: the supplier's quote no longer matches this "
            "request. Request a new quote before ordering."
        ))
    return QuoteResolution(quote=newest)


def apply_to_selection(selection: dict[str, Any], quote: dict[str, Any]) -> dict[str, Any]:
    """Overwrite a selection's commercial terms with the accepted quote's.

    Price, currency, quantity and lead time come from the quote; ``quote_id`` records
    the provenance. Returns the same dict (mutated in place, mirroring the selection
    builders' style).
    """
    price = quote.get("unit_price")
    if price is not None:
        selection["unit_price"] = price
    selection["currency"] = quote.get("currency") or selection.get("currency") or "USD"
    if quote.get("lead_time"):
        selection["lead_time"] = quote["lead_time"]
    if quote.get("quantity"):
        try:
            selection["quantity"] = int(quote["quantity"])
        except (TypeError, ValueError):
            pass
    selection["quote_id"] = quote.get("id")
    selection["source"] = "quote"
    return selection
