"""T1 / ruling R1 (evaluation finding F-07) — an accepted structured quote IS the price.

Every fixture in this file is the evaluation's own S1 evidence, copied verbatim into
``fixtures/eval_s1_quote_order.json`` from:

  * ``eval/e2e-flags-on:eval/e2e/evidence/s1_step9_buyer_view.json``
    — the buyer card: DXP Enterprises, price 189.0, leadTime "2 days",
      ``evidenceState: "quoted"``, ``quoteId: c9639b15-...``, url ``https://dxpe.com``.
  * ``eval/e2e-flags-on:eval/e2e/evidence/s1_step10_accept_order.json``
    — the order the buyer actually got: ``unit_price: null``, ``status: "draft"``,
      ``placed: false``.
  * ``eval/e2e-flags-on:eval/e2e/evidence/verify/verify_offline_checks.json`` -> ``F-07``
    — the read-only DB pair: order ``unit_price NULL`` / quote ``active`` at 189.0,
      and ``order_path_references_quote_store: false``.

The invariant under test, from the brief: **no path may substitute a listing price for
an accepted quote.**
"""

import json
from pathlib import Path

import pytest

from utils import order_quote, orders, price_db, quote_store
from utils.models import SourcingRun
from utils.procurement_agent.agents.procurement_agent import ProcurementAgent

_EVIDENCE = json.loads(
    (Path(__file__).parent / "fixtures" / "eval_s1_quote_order.json").read_text(encoding="utf-8")
)

RUN_ID = _EVIDENCE["run_id"]
SPECS = _EVIDENCE["asset_specs"]
CANDIDATE_CARD = _EVIDENCE["quoted_candidate"]
QUOTE_ROW = _EVIDENCE["quote_row"]

_APPROVED = [{"sequence": 1, "action": "approved", "approver_name": "Maintenance Director"}]


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """orders + price_db + quote_store all in tmp, and QUOTE_SUBMIT_V1 on.

    quote_store is isolated deliberately (gate finding F-E): its ``_DATA_DIR`` is the
    repo's real ``data/`` and a quote read must never touch a developer's DB.
    """
    monkeypatch.setattr(orders, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(orders, "_DB_PATH", str(tmp_path / "orders.sqlite"))
    monkeypatch.setattr(price_db, "_DB_PATH", str(tmp_path / "price_db.json"))
    monkeypatch.setattr(quote_store, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(quote_store, "_DB_PATH", str(tmp_path / "quotes.sqlite"))
    monkeypatch.setenv("QUOTE_SUBMIT_V1", "1")
    return orders


def _s1_candidate(**over):
    """The S1 DXP candidate in the RAW sourcing-results shape the order path resolves.

    The buyer card's overlaid price/quoteId are deliberately NOT carried here — the
    order path sees the un-overlaid row, which is exactly why F-07 happened.
    """
    cand = {
        "vendor_name": CANDIDATE_CARD["vendorName"],
        "source_url": CANDIDATE_CARD["url"],
        "requires_rfq": True,
        "price_tbd": True,
    }
    cand.update(over)
    return cand


def _run(candidate, **over):
    kw = dict(
        id=RUN_ID,
        asset_specs_json=SPECS,
        selected_candidate_json=candidate,
        approval_history_json=_APPROVED,
        current_phase="approved",
    )
    kw.update(over)
    return SourcingRun(**kw)


def _submit_s1_quote(**over):
    """Re-create the S1 quote row (dxpe.com, 189.0, '2 days', active)."""
    kw = dict(
        supplier_domain=QUOTE_ROW["domain"],
        unit_price=QUOTE_ROW["unit_price"],
        submitted_via="portal",
        run_id=RUN_ID,
        vendor_name=QUOTE_ROW.get("vendor") or CANDIDATE_CARD["vendorName"],
        lead_time=QUOTE_ROW["lead_time"],
        currency="USD",
    )
    kw.update(over)
    q = quote_store.submit_quote(**kw)
    assert q is not None, "the S1 quote must land — QUOTE_SUBMIT_V1 is on in this fixture"
    return q


class TestAcceptedQuotePricesTheOrder:
    """The S1 break: accepting the $189 DXP quote must produce a $189 order."""

    def test_s1_accepted_quote_prices_the_order_and_records_the_quote_id(self, isolated):
        quote = _submit_s1_quote()
        res = ProcurementAgent()._execute(_run(_s1_candidate()))

        assert res["success"] is True
        assert res["placed"] is True, (
            "evidence s1_step10_accept_order.json observed placed=false with "
            "unit_price=null; with the quote consulted the order must place"
        )
        order = res["order"]
        assert order["unit_price"] == QUOTE_ROW["unit_price"] == 189.0
        assert order["currency"] == "USD"
        assert order["lead_time"] == QUOTE_ROW["lead_time"] == "2 days"
        assert order["quote_id"] == quote["id"]
        assert order["status"] == orders.STATUS_PLACED

    def test_the_quote_beats_a_listing_price_on_the_same_candidate(self, isolated):
        """A candidate carrying BOTH a listing price and an accepted quote takes the quote."""
        _submit_s1_quote()
        listing = _s1_candidate(price_tbd=False, requires_rfq=False, base_price=402.55)
        res = ProcurementAgent()._execute(_run(listing))

        assert res["order"]["unit_price"] == 189.0, "the listing price must never win"
        assert res["order"]["unit_price"] != 402.55
        assert res["order"]["quote_id"] is not None

    def test_the_quote_beats_a_price_db_entry_for_the_same_part(self, isolated):
        """The price_db fallback must not stand in for the quote either."""
        price_db.save_price(SPECS["manufacturer"], "155-CART",
                            CANDIDATE_CARD["vendorName"], 999.0, source="rfq")
        _submit_s1_quote()
        res = ProcurementAgent()._execute(
            _run(_s1_candidate(), asset_specs_json=dict(SPECS, part_number="155-CART")))
        assert res["order"]["unit_price"] == 189.0


class TestStaleQuoteRefusesRatherThanFallsBack:
    """R1: expired / withdrawn => an explicit reason, and nothing is placed."""

    def test_withdrawn_quote_refuses_with_a_reason_and_places_nothing(self, isolated):
        quote = _submit_s1_quote()
        quote_store.withdraw(quote["id"], resolved_by="supplier")

        listing = _s1_candidate(price_tbd=False, requires_rfq=False, base_price=402.55)
        res = ProcurementAgent()._execute(_run(listing))

        assert res["success"] is False
        assert res["placed"] is False
        assert res["order"] is None
        assert "withdrew" in res["message"]
        assert orders.get_orders(run_id=RUN_ID) == [], "nothing may be captured on a refusal"

    def test_expired_quote_refuses_and_does_not_fall_back_to_the_listing_price(self, isolated):
        _submit_s1_quote(valid_until="2020-01-01T00:00:00+00:00")

        listing = _s1_candidate(price_tbd=False, requires_rfq=False, base_price=402.55)
        res = ProcurementAgent()._execute(_run(listing))

        assert res["success"] is False and res["placed"] is False
        assert "expired" in res["message"]
        assert orders.get_orders(run_id=RUN_ID) == []

    def test_a_quote_under_review_is_not_an_accepted_quote(self, isolated):
        """A review-status quote (the wrong-part gate) neither prices nor is ignored."""
        _submit_s1_quote(requested_part_number="155-CART", quoted_part_number="999-OTHER")
        res = ProcurementAgent()._execute(_run(_s1_candidate()))
        assert res["success"] is False
        assert "review" in res["message"]


class TestNoQuoteKeepsTheExistingPath:
    """Absent a quote, today's behaviour is unchanged — including the honest draft."""

    def test_no_quote_no_price_shows_unpriced_needs_a_quote(self, isolated):
        res = ProcurementAgent()._execute(_run(_s1_candidate()))
        assert res["success"] is True and res["placed"] is False
        assert res["order"]["status"] == orders.STATUS_DRAFT
        assert res["order"]["unit_price"] is None
        assert res["order"]["quote_id"] is None
        assert "unpriced" in res["message"] and "needs a quote" in res["message"]

    def test_no_quote_with_a_listing_price_still_places_on_the_listing_price(self, isolated):
        listing = _s1_candidate(price_tbd=False, requires_rfq=False, base_price=402.55)
        res = ProcurementAgent()._execute(_run(listing))
        assert res["placed"] is True
        assert res["order"]["unit_price"] == 402.55
        assert res["order"]["quote_id"] is None

    def test_flag_off_is_a_no_op(self, isolated, monkeypatch):
        """With QUOTE_SUBMIT_V1 off the quote is invisible and the listing price stands."""
        _submit_s1_quote()
        monkeypatch.setenv("QUOTE_SUBMIT_V1", "")
        listing = _s1_candidate(price_tbd=False, requires_rfq=False, base_price=402.55)
        res = ProcurementAgent()._execute(_run(listing))
        assert res["order"]["unit_price"] == 402.55
        assert res["order"]["quote_id"] is None


class TestResolverUnit:
    """``order_quote.resolve_for_order`` — the single decision point."""

    def test_absent_when_no_quote_exists(self, isolated):
        r = order_quote.resolve_for_order(RUN_ID, CANDIDATE_CARD["url"])
        assert r.priced is False and r.refused is False

    def test_domain_is_matched_the_way_the_buyer_card_matched_it(self, isolated):
        _submit_s1_quote()
        r = order_quote.resolve_for_order(RUN_ID, "https://www.dxpe.com/some/product/page")
        assert r.priced is True
        assert r.quote["unit_price"] == 189.0

    def test_a_quote_on_another_run_never_prices_this_one(self, isolated):
        _submit_s1_quote(run_id="some-other-run")
        r = order_quote.resolve_for_order(RUN_ID, CANDIDATE_CARD["url"])
        assert r.priced is False and r.refused is False

    def test_an_unreadable_quote_store_refuses_rather_than_reporting_absence(
            self, isolated, monkeypatch):
        def boom(**_kw):
            raise RuntimeError("quotes.sqlite is locked")

        monkeypatch.setattr(quote_store, "get_quotes", boom)
        r = order_quote.resolve_for_order(RUN_ID, CANDIDATE_CARD["url"])
        assert r.refused is True, "a failed read is not proof that no quote exists"
        assert order_quote.UNVERIFIABLE_REASON in r.refusal


class TestOrderNowPathObeysTheSameRule:
    """Gate finding F-C: ``/order-now`` is the SECOND order-creating path.

    R1 has to hold there too — it took the reconstructed listing price directly and
    422'd a price-less candidate even when an active quote for that supplier existed.
    """

    @pytest.fixture
    def api(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        from sqlalchemy.orm import sessionmaker

        from utils import site_settings, supplier_registry
        from utils.procurement_agent.state import persistence

        engine = persistence._make_engine(f"sqlite:///{tmp_path / 'api.sqlite'}")
        TestSession = sessionmaker(bind=engine, expire_on_commit=False)
        persistence.Base.metadata.create_all(engine)

        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        monkeypatch.setenv("TAVILY_API_KEY", "")
        monkeypatch.setenv("QUOTE_SUBMIT_V1", "1")
        monkeypatch.setattr(persistence, "_engine", engine)
        monkeypatch.setattr(persistence, "_SessionFactory", TestSession)
        for mod, name in ((supplier_registry, "supplier_registry"), (orders, "orders"),
                          (site_settings, "site_settings"), (quote_store, "quotes")):
            monkeypatch.setattr(mod, "_DATA_DIR", str(tmp_path))
            monkeypatch.setattr(mod, "_DB_PATH", str(tmp_path / f"{name}.sqlite"))
        monkeypatch.setattr(price_db, "_DB_PATH", str(tmp_path / "price_db.json"))

        import api_server
        monkeypatch.setattr(api_server, "_engine", engine)
        monkeypatch.setattr(api_server, "_SessionFactory", TestSession)
        client = TestClient(api_server.app)
        client._persistence = persistence
        return client

    def _setup(self, api, *, price_tbd=False, base_price=402.55):
        p = api._persistence
        run = p.create_run(facility_id="fac-s1", company_id="PIN-1",
                           asset_specs={"manufacturer": SPECS["manufacturer"],
                                        "part_number": None})
        cand = {"vendor_name": CANDIDATE_CARD["vendorName"],
                "source_url": CANDIDATE_CARD["url"]}
        if price_tbd:
            cand.update({"price_tbd": True, "requires_rfq": True})
        else:
            cand["base_price"] = base_price
        p.update_run(run["id"], {"sourcing_results_json": {"tier_1": {"results": [cand]}},
                                 "current_phase": "comparison"})
        p.upsert_approval_rule("fac-s1", 0, 0, [])       # sub-threshold: order created now
        return run["id"]

    def _post(self, api, rid):
        return api.post(f"/api/runs/{rid}/order-now",
                        json={"candidate_id": f"{CANDIDATE_CARD['vendorName']}-t1-0",
                              "tier": 1, "quantity": 1})

    def test_order_now_takes_the_quote_not_the_listing_price(self, api):
        rid = self._setup(api)
        _submit_s1_quote(run_id=rid)
        o = self._post(api, rid).json()["order"]
        assert o["unit_price"] == 189.0 and o["unit_price"] != 402.55
        assert o["quote_id"] is not None
        assert o["lead_time"] == "2 days"

    def test_order_now_prices_a_quote_only_candidate_instead_of_422(self, api):
        """The S1 shape: price_tbd candidate WITH an accepted quote is now orderable."""
        rid = self._setup(api, price_tbd=True)
        _submit_s1_quote(run_id=rid)
        r = self._post(api, rid)
        assert r.status_code == 200
        assert r.json()["order"]["unit_price"] == 189.0

    def test_order_now_still_422s_a_priceless_candidate_with_no_quote(self, api):
        rid = self._setup(api, price_tbd=True)
        r = self._post(api, rid)
        assert r.status_code == 422
        assert "request a quote" in r.json()["detail"].lower()

    def test_order_now_refuses_on_a_withdrawn_quote_rather_than_falling_back(self, api):
        rid = self._setup(api)
        q = _submit_s1_quote(run_id=rid)
        quote_store.withdraw(q["id"], resolved_by="supplier")
        r = self._post(api, rid)
        assert r.status_code == 409
        assert "withdrew" in r.json()["detail"]
        assert orders.get_orders(run_id=rid) == []
