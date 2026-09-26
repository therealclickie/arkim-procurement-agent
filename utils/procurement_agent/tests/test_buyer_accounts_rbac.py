"""
Arc 6 T2 — the buyer permission matrix (D2).

The matrix is the specification: EXPECTED below is D2's table written out as
data, and the table test checks every role × capability pair against the
module. A source-scan test then fails on any inline role comparison in a
buyer-facing route handler, enumerated from the running app's routes (so a new
buyer route is scanned without anyone remembering to list it).
"""
from __future__ import annotations

import inspect
import re

import pytest
from fastapi import FastAPI, Header
from fastapi.testclient import TestClient

from utils import buyer_accounts as ba
from utils import buyer_accounts_rbac as rbac
from utils.procurement_agent.tests._buyer_fixtures import buyer_facing_routes

R, B, A, AD = ba.ROLE_REQUESTER, ba.ROLE_BUYER, ba.ROLE_APPROVER, ba.ROLE_ADMIN

# D2's table, row by row. ✓ = the role set that holds the capability.
EXPECTED: dict[str, set[str]] = {
    rbac.RAISE_REQUEST:          {R, B, A, AD},
    rbac.VIEW_COMPANY:           {R, B, A, AD},
    rbac.SELECT_AND_ORDER:       {B, A, AD},
    rbac.APPROVE_WITHIN_LIMIT:   {B, A, AD},
    rbac.SECOND_APPROVAL:        {A, AD},
    rbac.OVERRIDE_LIMIT:         {AD},          # ¹ only while the setting is on
    rbac.SET_APPROVAL_LIMIT:     {AD},
    rbac.TOGGLE_ADMIN_OVERRIDE:  {AD},
    rbac.MANAGE_MEMBERS:         {AD},
}

_OVERRIDE_ON = {"allow_admin_override": True}
_OVERRIDE_OFF = {"allow_admin_override": False}


def _m(role, status=ba.MEMBER_ACTIVE, company_id="company-bayfoods", mid="m-1"):
    return {"id": mid, "role": role, "status": status, "company_id": company_id}


class TestMatrixTable:
    def test_expected_covers_every_capability_and_role(self):
        assert set(EXPECTED) == set(rbac.CAPABILITIES)
        assert set(rbac.CAPABILITY_MATRIX) == set(ba.ROLES)

    @pytest.mark.parametrize("capability", sorted(EXPECTED))
    @pytest.mark.parametrize("role", list(ba.ROLES))
    def test_every_role_capability_pair(self, role, capability):
        expected = role in EXPECTED[capability]
        assert rbac.has_permission(_m(role), capability, _OVERRIDE_ON) is expected

    @pytest.mark.parametrize("role", list(ba.ROLES))
    def test_override_needs_the_company_setting(self, role):
        assert rbac.has_permission(_m(role), rbac.OVERRIDE_LIMIT, _OVERRIDE_OFF) is False
        assert rbac.has_permission(_m(role), rbac.OVERRIDE_LIMIT, None) is False

    @pytest.mark.parametrize("capability", sorted(EXPECTED))
    def test_fail_closed(self, capability):
        assert rbac.has_permission(None, capability, _OVERRIDE_ON) is False
        assert rbac.has_permission(_m(AD, status=ba.MEMBER_REVOKED), capability,
                                   _OVERRIDE_ON) is False
        assert rbac.has_permission(_m("OWNER"), capability, _OVERRIDE_ON) is False
        assert rbac.has_permission(_m(AD), "no_such_capability", _OVERRIDE_ON) is False

    def test_permissions_for_reflects_the_matrix(self):
        assert rbac.permissions_for(_m(R)) == sorted({rbac.RAISE_REQUEST, rbac.VIEW_COMPANY})
        assert rbac.OVERRIDE_LIMIT in rbac.permissions_for(_m(AD), _OVERRIDE_ON)
        assert rbac.OVERRIDE_LIMIT not in rbac.permissions_for(_m(AD), _OVERRIDE_OFF)


class TestCapabilityDependency:
    def _app(self, session):
        app = FastAPI()

        def fake_session(x_role: str = Header(default="")):
            return session(x_role)

        @app.get("/t")
        def t(s=__import__("fastapi").Depends(
                rbac.capability_dependency(rbac.SELECT_AND_ORDER, fake_session))):
            return {"ok": True, "session": s is not None}
        return TestClient(app)

    def test_role_with_capability_passes_and_without_is_403(self):
        client = self._app(lambda role: {"member": _m(role), "company": _OVERRIDE_ON})
        assert client.get("/t", headers={"x-role": B}).status_code == 200
        r = client.get("/t", headers={"x-role": R})
        assert r.status_code == 403 and r.json() == {"detail": "Forbidden"}

    def test_none_session_passes_through_unchanged(self):
        # A session dependency returning None means "identity not in force"
        # (flag off on a pre-existing route): the check must not invent a 403.
        client = self._app(lambda role: None)
        assert client.get("/t").json() == {"ok": True, "session": False}

    def test_dependency_carries_its_capability_marker(self):
        dep = rbac.capability_dependency(rbac.VIEW_COMPANY, lambda: None)
        assert dep.buyer_capability == rbac.VIEW_COMPANY


# ---------------------------------------------------------------------------
# Source scan: no inline role comparison in any buyer-facing route handler.
# ---------------------------------------------------------------------------

# 1-2: any buyer role NAME in a handler (a literal or the store constant) —
#      there is no legitimate reason for a route to name one.
# 3:   a comparison on the role field of a person-shaped object (member,
#      actor, session, buyer, caller, user). Chat messages also have a
#      "role" ("agent"/"user" turns) — those are not people and not matched.
_FORBIDDEN = (
    re.compile(r"""["'](REQUESTER|BUYER|APPROVER|ADMIN)["']"""),
    re.compile(r"\bROLE_(REQUESTER|BUYER|APPROVER|ADMIN)\b"),
    re.compile(r"""\b\w*(member|actor|session|buyer|caller|user)\w*"""
               r"""(\[["']member["']\])?"""
               r"""(\.role|\[["']role["']\]|\.get\(["']role["']\))\s*(==|!=|\bin\b|\bnot in\b)"""),
)


def _buyer_handlers():
    import api_server
    seen, out = set(), []
    for route in buyer_facing_routes(api_server.app):
        fn = route.endpoint
        if fn in seen:
            continue
        seen.add(fn)
        out.append((route.path, fn))
    return out


class TestNoInlineRoleChecks:
    def test_the_scan_sees_the_buyer_routes(self):
        paths = {p for p, _ in _buyer_handlers()}
        assert "/api/runs/{run_id}/approve" in paths
        assert len(paths) >= 45

    def test_no_buyer_handler_compares_roles_inline(self):
        offenders = []
        for path, fn in _buyer_handlers():
            src = inspect.getsource(fn)
            for pat in _FORBIDDEN:
                m = pat.search(src)
                if m:
                    offenders.append(f"{path} ({fn.__name__}): {m.group(0)!r}")
        assert not offenders, "inline role checks in buyer routes:\n" + "\n".join(offenders)

    @pytest.mark.parametrize("line", [
        'if member["role"] == "ADMIN": pass',
        'if session["member"]["role"] in allowed: pass',
        'if actor.get("role") != wanted: pass',
        'if buyer.role == x: pass',
        'if ba.ROLE_ADMIN: pass',
    ])
    def test_the_scan_would_catch_an_offender(self, line):
        assert any(p.search(line) for p in _FORBIDDEN), line

    def test_chat_turn_roles_are_not_people(self):
        assert not any(p.search('if msg["role"] == "agent": pass') for p in _FORBIDDEN)


# ---------------------------------------------------------------------------
# Member-management policy (store-backed)
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(ba, "_DB_PATH", str(tmp_path / "buyer_accounts.sqlite"))
    monkeypatch.setenv("BUYER_ACCOUNTS_V1", "1")
    ba.create_company("company-bayfoods", "Bay Foods", facility_ids=["fac-stockton"])
    ba.create_company("company-northgate", "Northgate", facility_ids=["fac-ng"])
    admin = ba.add_member("company-bayfoods", "admin@bayfoods.com", role=AD)
    return admin


class TestMemberPolicy:
    def test_invite_defaults_to_requester(self, store):
        m = rbac.invite_member(store, "new@bayfoods.com")
        assert m["role"] == R and m["company_id"] == "company-bayfoods"
        assert m["invited_by"] == store["id"]

    @pytest.mark.parametrize("role", [R, B, A])
    def test_only_admin_manages(self, store, role):
        actor = ba.add_member("company-bayfoods", f"{role.lower()}@bayfoods.com", role=role)
        target = ba.add_member("company-bayfoods", "t@bayfoods.com")
        for call in (lambda: rbac.invite_member(actor, "x@bayfoods.com"),
                     lambda: rbac.change_member_role(actor, target["id"], B),
                     lambda: rbac.revoke_member(actor, target["id"])):
            with pytest.raises(ba.BuyerAccountsError) as exc:
                call()
            assert exc.value.code == "forbidden"

    def test_last_admin_cannot_demote_or_revoke_themselves(self, store):
        for call in (lambda: rbac.change_member_role(store, store["id"], B),
                     lambda: rbac.revoke_member(store, store["id"])):
            with pytest.raises(ba.BuyerAccountsError) as exc:
                call()
            assert exc.value.code == "last_admin"
        assert ba.get_member(store["id"])["role"] == AD

    def test_a_second_admin_unlocks_it(self, store):
        other = rbac.invite_member(store, "two@bayfoods.com", AD)
        rbac.change_member_role(store, store["id"], B)
        assert ba.get_member(store["id"])["role"] == B
        with pytest.raises(ba.BuyerAccountsError) as exc:
            rbac.revoke_member(other, other["id"])
        assert exc.value.code == "last_admin"

    def test_cross_company_target_is_not_found(self, store):
        foreign = ba.add_member("company-northgate", "x@northgate.com")
        for call in (lambda: rbac.change_member_role(store, foreign["id"], B),
                     lambda: rbac.revoke_member(store, foreign["id"])):
            with pytest.raises(ba.BuyerAccountsError) as exc:
                call()
            assert exc.value.code == "member_not_found"
        with pytest.raises(ba.BuyerAccountsError) as exc:
            rbac.revoke_member(store, "no-such-id")
        assert exc.value.code == "member_not_found"

    def test_invite_of_someone_in_another_company_is_refused(self, store):
        ba.add_member("company-northgate", "x@northgate.com")
        with pytest.raises(ba.BuyerAccountsError) as exc:
            rbac.invite_member(store, "x@northgate.com")
        assert exc.value.code == "already_member"
