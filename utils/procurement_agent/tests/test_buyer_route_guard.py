"""
Arc 6 T4 — the STRUCTURAL guard for company isolation (D6).

A per-endpoint list protects only the endpoints someone remembered. This test
enumerates every route registered on the RUNNING app (``app.routes`` — not the
source text, not the K2 list), classifies each by an explicit rule
(``_buyer_fixtures.is_buyer_facing``), and fails if a buyer-facing route lacks
the buyer-session door. A new buyer endpoint added by any future arc fails here
until it is scoped — or until someone adds it to ``EXEMPT`` below, which is a
visible, reviewable change with a written reason.
"""
from __future__ import annotations

from fastapi.routing import APIRoute

from utils.procurement_agent.tests._buyer_fixtures import (  # noqa: F401
    ADMIN_TOKEN, buyer_api, buyer_api_off, buyer_facing_routes, is_buyer_facing,
    origin_headers,
)

# ---------------------------------------------------------------------------
# THE exemptions. Short, named, each with its reason. (method, path) -> kind.
#   "public"   — reachable without a session by design; no door.
#   "disabled" — not a buyer surface; returns 404 under BUYER_ACCOUNTS_V1.
# ---------------------------------------------------------------------------
EXEMPT: dict[tuple[str, str], tuple[str, str]] = {
    ("POST", "/api/buyer/auth/request-link"): (
        "public", "requesting a sign-in link cannot require the session it leads to"),
    ("POST", "/api/buyer/auth/verify"): (
        "public", "verifying a sign-in link is how a session is obtained"),
    ("POST", "/api/runs/from-maintenance"): (
        "disabled", "disabled under BUYER_ACCOUNTS_V1 until service auth is enforced "
                    "(gate Q2: get_caller returns None without a token and the service "
                    "signature is never required)"),
    ("GET", "/api/debug/llm"): ("disabled", "disabled under BUYER_ACCOUNTS_V1 (gate Q3)"),
    ("POST", "/api/dev/reseed-handoffs"): ("disabled", "disabled under BUYER_ACCOUNTS_V1 (gate Q3)"),
}

# Path parameters the door knows how to scope (or scopes by construction).
KNOWN_PATH_PARAMS = {"run_id", "group_id", "draft_id", "item_id", "facility_id",
                     "site_id", "member_id"}


def _calls(dependant):
    """Every dependency callable in a route's dependency tree."""
    out = []
    for d in dependant.dependencies:
        out.append(d.call)
        out.extend(_calls(d))
    return out


def _routes_by_key(app):
    out = {}
    for r in buyer_facing_routes(app):
        for m in r.methods:
            out[(m, r.path)] = r
    return out


class TestStructuralGuard:
    def test_the_rule_classifies_every_registered_route(self):
        import api_server
        api_routes = [r for r in api_server.app.routes if isinstance(r, APIRoute)]
        non_api = [r for r in api_server.app.routes if not isinstance(r, APIRoute)]
        # The four FastAPI auto-routes are the only non-APIRoutes.
        assert sorted(getattr(r, "path", "") for r in non_api) == sorted(
            ["/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"])
        buyer = buyer_facing_routes(api_server.app)
        # Non-vacuous: the 45 K2 routes + 3 exempt-disabled + 2 public + the new
        # /api/buyer/* surface.
        assert len(buyer) >= 50
        assert len(api_routes) > len(buyer)

    def test_every_buyer_facing_route_has_the_door(self):
        import api_server
        missing = []
        for key, route in sorted(_routes_by_key(api_server.app).items()):
            calls = _calls(route.dependant)
            if key in EXEMPT:
                kind, _reason = EXEMPT[key]
                if kind == "disabled" and api_server._buyer_disabled_under_flag not in calls:
                    missing.append(f"{key}: exempt as disabled but lacks _buyer_disabled_under_flag")
                continue
            has_session = (api_server._buyer_session_in_force in calls
                           or api_server._require_buyer_session in calls)
            has_capability = any(getattr(c, "buyer_capability", None) for c in calls)
            if not (has_session and has_capability):
                missing.append(f"{key}: session={has_session} capability={has_capability}")
        assert not missing, "buyer-facing routes without the door:\n" + "\n".join(missing)

    def test_every_exemption_is_a_real_route(self):
        # A stale exemption would silently cover a future route of that name.
        import api_server
        keys = set(_routes_by_key(api_server.app))
        assert set(EXEMPT) <= keys, set(EXEMPT) - keys

    def test_the_exemption_list_stays_short_and_reasoned(self):
        assert len(EXEMPT) <= 5
        for key, (kind, reason) in EXEMPT.items():
            assert kind in ("public", "disabled"), key
            assert len(reason) > 20, key

    def test_every_path_parameter_is_one_the_door_scopes(self):
        import api_server
        unknown = {}
        for key, route in _routes_by_key(api_server.app).items():
            params = set(route.param_convertors)
            extra = params - KNOWN_PATH_PARAMS
            if extra:
                unknown[key] = extra
        assert not unknown, unknown
        # And the door's own table agrees with this list (minus the /api/buyer
        # member routes, which scope in the rbac module, and site_id, which is
        # scoped by construction).
        assert set(api_server._BUYER_SCOPE_NOT_FOUND) | set(
            api_server._BUYER_SCOPE_BY_CONSTRUCTION) | {"member_id"} == KNOWN_PATH_PARAMS

    def test_the_guard_would_catch_an_unscoped_route(self):
        import api_server
        from fastapi import FastAPI
        probe = FastAPI()

        @probe.get("/api/new-buyer-thing/{run_id}")
        def new_thing(run_id: str):
            return {}

        route = [r for r in probe.routes if isinstance(r, APIRoute)][0]
        assert is_buyer_facing(route.path)
        calls = _calls(route.dependant)
        assert api_server._buyer_session_in_force not in calls


class TestRuntimeNoSessionNoEntry:
    """Belt to the structural braces: call EVERY buyer-facing route on the
    running app with the flag on and no session. Each must be refused before
    its handler runs — 401 at the door, or 404 for the disabled routes."""

    def _fill(self, path):
        for p in KNOWN_PATH_PARAMS:
            path = path.replace("{" + p + "}", "x-" + p)
        return path

    def test_no_buyer_route_is_reachable_without_a_session(self, buyer_api):
        import api_server
        problems = []
        for (method, path), route in sorted(_routes_by_key(api_server.app).items()):
            kind = EXEMPT.get((method, path), (None, ""))[0]
            if kind == "public":
                continue
            r = buyer_api.request(method, self._fill(path), json={},
                                  headers={**origin_headers(),
                                           # a bearer is not a buyer credential (Q1)
                                           "Authorization": f"Bearer {ADMIN_TOKEN}"})
            want = 404 if kind == "disabled" else 401
            if r.status_code != want:
                problems.append(f"{method} {path}: {r.status_code} (want {want})")
        assert not problems, "\n".join(problems)

    def test_disabled_routes_are_the_plain_404_under_the_flag(self, buyer_api):
        for method, path in [("POST", "/api/runs/from-maintenance"),
                             ("GET", "/api/debug/llm"),
                             ("POST", "/api/dev/reseed-handoffs")]:
            r = buyer_api.request(method, path, json={})
            assert r.status_code == 404 and r.json() == {"detail": "Not Found"}, path

    def test_from_maintenance_unsigned_call_is_refused_under_the_flag(self, buyer_api):
        body = {"submission_id": "s-1", "facility_id": "fac-stockton",
                "context": {"urgency": "standard"}}
        r = buyer_api.post("/api/runs/from-maintenance", json=body,
                           headers={"X-Arkim-Service-Signature": "forged"})
        assert r.status_code == 404


class TestFlagOffUnchanged:
    def test_disabled_routes_behave_as_today_with_the_flag_off(self, buyer_api_off):
        r = buyer_api_off.get("/api/debug/llm")
        assert r.status_code == 200
        assert r.json()["ok"] is False          # no key in tests; no network
        r = buyer_api_off.post("/api/dev/reseed-handoffs")
        assert r.status_code in (200, 404)      # 404 only if the fixture file is absent
        assert r.json() != {"detail": "Not Found"}

    def test_buyer_routes_need_no_session_with_the_flag_off(self, buyer_api_off):
        assert buyer_api_off.get("/api/runs").status_code == 200
        assert buyer_api_off.get("/api/orders").status_code == 200
        r = buyer_api_off.post("/api/runs", json={})
        assert r.status_code == 201
