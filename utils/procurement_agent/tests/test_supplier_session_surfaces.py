"""
Arc 3 (gate findings F1 / F2) — the SESSION doors arc 2 left token-only.

Arc 2 shipped a session surface with no profile read, no propose-revision and
no quote history, and a ``me`` that carried no capability list — so T6/T7/T11
had nothing to build on. These are their backend halves:

  - ``member.permissions`` on ``GET /api/supplier/me``   (F1)
  - ``GET  /api/supplier/profile``                        (F2)
  - ``POST /api/supplier/propose-revision``               (F2)
  - ``GET  /api/supplier/quotes``  (history)              (F2)

The load-bearing property is that each goes through the SAME service its
``/api/portal/{token}`` sibling uses, so the two doors cannot drift. That is
asserted directly: both doors are called in one test and the bodies compared.
"""
from __future__ import annotations

import pytest

from utils.procurement_agent.tests._arc3_session_fixtures import (  # noqa: F401
    APP_ORIGIN, FOREIGN_ORIGIN, active_member, magic_link_token, sess_api,
    sess_api_http, verify,
)


@pytest.fixture
def portal_on(monkeypatch):
    """The profile surfaces live in utils/supplier_portal, which is dormant
    unless SUPPLIER_PORTAL_V1 is on — an honest second gate, not an oversight."""
    monkeypatch.setenv("SUPPLIER_PORTAL_V1", "1")


def _supplier_record(client, domain="dxpe.com", name="DXP Enterprises"):
    from utils import supplier_registry
    supplier_registry._ensure_supplier_row(domain, name=name)
    return domain


def _logged_in(client, monkeypatch, domain="dxpe.com", email="sales@dxpe.com",
               role=None):
    active_member(client, domain=domain, email=email, role=role)
    _supplier_record(client, domain)
    verify(client, monkeypatch, email=email)


# ---------------------------------------------------------------------------
# F1 — permissions on `me`
# ---------------------------------------------------------------------------

class TestMePermissions:
    def test_permissions_reflect_the_matrix_for_each_role(self, sess_api,
                                                          monkeypatch):
        from utils import supplier_accounts_rbac as rbac
        sa = sess_api._sa
        acct = sa.create_account("dxpe.com")
        sa.add_member(acct["id"], "owner@dxpe.com", role=sa.ROLE_OWNER,
                      status=sa.MEMBER_ACTIVE)
        sa.add_member(acct["id"], "admin@dxpe.com", role=sa.ROLE_ADMIN,
                      status=sa.MEMBER_ACTIVE)
        sa.add_member(acct["id"], "member@dxpe.com", role=sa.ROLE_MEMBER,
                      status=sa.MEMBER_ACTIVE)
        for email, role in (("owner@dxpe.com", sa.ROLE_OWNER),
                            ("admin@dxpe.com", sa.ROLE_ADMIN),
                            ("member@dxpe.com", sa.ROLE_MEMBER)):
            sess_api.cookies.clear()
            verify(sess_api, monkeypatch, email=email)
            perms = sess_api.get("/api/supplier/me").json()["member"]["permissions"]
            # Derived from THE matrix, not re-declared here — the whole point
            # of F1 is that the UI never reimplements the policy.
            assert set(perms) == set(rbac.CAPABILITY_MATRIX[role])

    def test_the_top_level_identity_shape_is_unchanged(self, sess_api,
                                                       monkeypatch):
        # Arc 2 pins set(body) == {"account","member"}; permissions is nested
        # under member precisely so that stays true.
        active_member(sess_api)
        verify(sess_api, monkeypatch)
        body = sess_api.get("/api/supplier/me").json()
        assert set(body.keys()) == {"account", "member"}
        assert "permissions" in body["member"]

    def test_only_the_owner_holds_transfer_ownership(self, sess_api,
                                                     monkeypatch):
        active_member(sess_api)
        verify(sess_api, monkeypatch)
        perms = sess_api.get("/api/supplier/me").json()["member"]["permissions"]
        assert "transfer_ownership" in perms


# ---------------------------------------------------------------------------
# F2 — profile read, via the SAME assembly as the token door
# ---------------------------------------------------------------------------

class TestSessionProfile:
    def test_returns_the_account_domain_profile(self, sess_api, monkeypatch,
                                                portal_on):
        _logged_in(sess_api, monkeypatch)
        r = sess_api.get("/api/supplier/profile")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["supplier_domain"] == "dxpe.com"
        assert set(body.keys()) == {"teaser", "supplier_domain", "name",
                                    "brands", "classes", "ship_area",
                                    "aftermarket_disclosure"}

    def test_both_doors_return_the_identical_body(self, sess_api, monkeypatch,
                                                  portal_on):
        # The anti-drift assertion: one service, two doors. If a future change
        # edits one route's assembly only, this fails.
        _logged_in(sess_api, monkeypatch)
        from utils import claim_tokens
        monkeypatch.setattr(claim_tokens, "CLAIM_TOKENS_ENABLED", True)
        claim = claim_tokens.generate_for("dxpe.com")
        assert claim is not None
        via_session = sess_api.get("/api/supplier/profile")
        via_token = sess_api.get(f"/api/portal/{claim['token']}/profile")
        assert via_session.status_code == via_token.status_code == 200
        assert via_session.json() == via_token.json()

    def test_404_when_the_account_has_no_supplier_record(self, sess_api,
                                                         monkeypatch,
                                                         portal_on):
        active_member(sess_api)          # account, but no registry supplier
        verify(sess_api, monkeypatch)
        r = sess_api.get("/api/supplier/profile")
        assert r.status_code == 404

    def test_404_when_the_portal_module_is_dormant(self, sess_api, monkeypatch):
        # SUPPLIER_PORTAL_V1 off ⇒ read_profile returns None ⇒ honest 404.
        monkeypatch.setenv("SUPPLIER_PORTAL_V1", "0")
        _logged_in(sess_api, monkeypatch)
        assert sess_api.get("/api/supplier/profile").status_code == 404

    def test_the_session_cannot_be_pointed_at_another_domain(self, sess_api,
                                                             monkeypatch,
                                                             portal_on):
        # There is no domain parameter to point — the scope comes from the
        # validated session. Proven by rendering a SECOND supplier and showing
        # the session never sees it.
        _logged_in(sess_api, monkeypatch)
        _supplier_record(sess_api, "other.com", "Other Supply")
        body = sess_api.get("/api/supplier/profile").json()
        assert body["supplier_domain"] == "dxpe.com"


# ---------------------------------------------------------------------------
# F2 — propose-revision under the session identity
# ---------------------------------------------------------------------------

class TestSessionProposeRevision:
    def test_lands_a_pending_revision_with_member_provenance(self, sess_api,
                                                             monkeypatch,
                                                             portal_on):
        _logged_in(sess_api, monkeypatch)
        me = sess_api.get("/api/supplier/me").json()
        r = sess_api.post(
            "/api/supplier/propose-revision",
            headers={"Origin": APP_ORIGIN},
            json={"brands": [{"brand_id": "Goulds", "relationship": "CARRIES"}],
                  "classes": [{"class_id": "PUMP", "is_core": True}],
                  "ship_area": {"kind": "NATIONWIDE_US"}})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "pending"
        assert r.json()["ok"] is True

        # Nothing wrote the registry — the concierge approve is the only writer.
        from utils import supplier_registry
        assert supplier_registry.get_supplier_brands("dxpe.com") == []

        # The revision carries WHO proposed it (the one thing the token door
        # cannot offer).
        items = supplier_registry.get_review_items(kind="supplier_revision")
        assert len(items) == 1
        assert items[0]["payload"]["proposed_by"] == f"member:{me['member']['id']}"

    def test_malformed_brand_relationship_is_422(self, sess_api, monkeypatch,
                                                 portal_on):
        _logged_in(sess_api, monkeypatch)
        r = sess_api.post("/api/supplier/propose-revision",
                          headers={"Origin": APP_ORIGIN},
                          json={"brands": [{"brand_id": "Goulds",
                                            "relationship": "MADE_UP"}]})
        assert r.status_code == 422

    def test_it_is_csrf_protected_like_every_state_changing_route(
            self, sess_api, monkeypatch, portal_on):
        _logged_in(sess_api, monkeypatch)
        r = sess_api.post("/api/supplier/propose-revision",
                          headers={"Origin": FOREIGN_ORIGIN},
                          json={"ship_area": {"kind": "NATIONWIDE_US"}})
        assert r.status_code == 401

    def test_requires_the_propose_revisions_capability(self, sess_api,
                                                       monkeypatch, portal_on):
        # Every role holds it today, so this proves the WIRING, not a refusal:
        # strip the capability from the matrix and the route must 403. That is
        # the falsifiable form — "a MEMBER can also do it" would pass even if
        # the route were ungated.
        _logged_in(sess_api, monkeypatch)
        from utils import supplier_accounts_rbac as rbac
        monkeypatch.setitem(rbac.CAPABILITY_MATRIX, rbac.ROLE_OWNER,
                            frozenset())
        r = sess_api.post("/api/supplier/propose-revision",
                          headers={"Origin": APP_ORIGIN},
                          json={"ship_area": {"kind": "NATIONWIDE_US"}})
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# F2 — quote history
# ---------------------------------------------------------------------------

class TestSessionQuoteHistory:
    def test_empty_history_is_an_empty_list_not_a_fabrication(self, sess_api,
                                                              monkeypatch):
        monkeypatch.setenv("QUOTE_SUBMIT_V1", "1")
        _logged_in(sess_api, monkeypatch)
        r = sess_api.get("/api/supplier/quotes")
        assert r.status_code == 200, r.text
        assert r.json() == {"quotes": []}

    def test_both_doors_return_the_identical_history(self, sess_api,
                                                     monkeypatch, portal_on):
        monkeypatch.setenv("QUOTE_SUBMIT_V1", "1")
        _logged_in(sess_api, monkeypatch)
        from utils import claim_tokens
        monkeypatch.setattr(claim_tokens, "CLAIM_TOKENS_ENABLED", True)
        claim = claim_tokens.generate_for("dxpe.com")
        assert claim is not None
        via_session = sess_api.get("/api/supplier/quotes")
        via_token = sess_api.get(f"/api/portal/{claim['token']}/quotes")
        assert via_session.status_code == via_token.status_code == 200
        assert via_session.json() == via_token.json()

    def test_absent_when_the_quote_flag_is_off(self, sess_api, monkeypatch):
        monkeypatch.setenv("QUOTE_SUBMIT_V1", "0")
        _logged_in(sess_api, monkeypatch)
        r = sess_api.get("/api/supplier/quotes")
        assert r.status_code == 404
        assert r.json() == {"detail": "Not Found"}

    def test_post_to_the_same_path_is_still_the_submit_route(self, sess_api,
                                                             monkeypatch):
        # GET and POST share /api/supplier/quotes; adding the GET must not
        # have shadowed arc 2's POST. No open RFQ ⇒ its own honest 404.
        monkeypatch.setenv("QUOTE_SUBMIT_V1", "1")
        _logged_in(sess_api, monkeypatch)
        r = sess_api.post("/api/supplier/quotes", headers={"Origin": APP_ORIGIN},
                          json={"run_id": "run_missing", "quote_number": "Q1",
                                "unit_price": 100.0, "quantity": 1,
                                "lead_time": "in stock"})
        assert r.status_code == 404
        assert r.json() == {"detail": "No open request for this supplier"}


# ---------------------------------------------------------------------------
# Flag-off inertness for the new routes
# ---------------------------------------------------------------------------

class TestFlagOff:
    @pytest.mark.parametrize("method,path,kwargs", [
        ("get", "/api/supplier/profile", {}),
        ("post", "/api/supplier/propose-revision", {"json": {}}),
        ("get", "/api/supplier/quotes", {}),
    ])
    def test_absent_when_supplier_accounts_is_off(self, sess_api, monkeypatch,
                                                  method, path, kwargs):
        monkeypatch.setenv("SUPPLIER_ACCOUNTS_V1", "0")
        r = getattr(sess_api, method)(path, headers={"Origin": APP_ORIGIN},
                                      **kwargs)
        assert r.status_code == 404
        assert r.json() == {"detail": "Not Found"}
