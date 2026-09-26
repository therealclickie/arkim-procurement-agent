"""PH-01 round 3d — readiness is enforced INSIDE the one sourcing choke point.

The cloud review of round 3c found the channel consumer (email / SMS / voice)
started sourcing on the intake agent's own ``sufficient`` flag, reaching
``_commit_intake_to_sourcing`` with no ``intake_readiness.assess``, no identity
floor and no acknowledgement. The agent flag is more permissive than assess: a
bearing with dimensions but no manufacturer or model is sufficient but not ready.

Pinned here:
  - ``_commit_intake_to_sourcing`` refuses a not-ready request unless a recorded
    acknowledgement covers every missing group, before touching the run;
  - the reviewer's probe arriving by email fires NO sourcing and is replied to with
    the clarification naming manufacturer and model; a ready email still sources;
  - ``_commit_intake_to_sourcing`` is the ONLY function that starts a sourcing run
    (structural — no future caller can bypass it);
  - the nameplate reply uses the chat's ready wording when assess says ready.
"""
from __future__ import annotations

import ast
import pathlib
from unittest.mock import Mock

import pytest

from utils import intake_readiness, intake_sufficiency
from utils.procurement_agent.tests.test_intake_api import (  # noqa: F401 (fixtures)
    _email_payload, _mock_intake_sufficient, api, intake_api,
)

#: The reviewer's probe: a bearing described by its dimensions, no manufacturer or
#: model. The intake agent may call this sufficient; assess does not call it ready.
BEARING_BY_DIMENSIONS = {
    "detected_type": "bearing", "category": "bearing",
    "description": "Deep groove ball bearing, 25mm bore, 52mm OD, 15mm wide",
    "bore_diameter": "25mm", "outer_diameter": "52mm", "width": "15mm",
}

#: Clears the identity floor and is not hygienic.
READY_PUMP = {"manufacturer": "Goulds", "model": "3196", "part_number": "3196MTX"}

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]


def _capture_replies(api, monkeypatch) -> list:
    replies: list = []
    monkeypatch.setattr(api._api_server, "_intake_reply_sink", replies.append)
    return replies


def _record_sourcing(api, monkeypatch) -> list:
    """Replace the background sourcing task with a recorder: each started sourcing
    run appends its run id (offline, and independent of the known-parts cache)."""
    started: list = []
    monkeypatch.setattr(api._api_server, "_run_sourcing_background",
                        lambda run_id, *a, **k: started.append(run_id))
    return started


def _intake_run(api, specs: dict) -> str:
    """A run at Phase.INTAKE carrying ``specs``, created through the API."""
    rid = api.post("/api/runs", json={}).json()["id"]
    api.put(f"/api/runs/{rid}/asset-specs", json={"asset_specs": specs})
    return rid


# ---------------------------------------------------------------------------
# The choke point refuses
# ---------------------------------------------------------------------------

class TestCommitRefusesNotReady:
    def test_not_ready_specs_without_acknowledgement_are_refused(self, api):
        server = api._api_server
        rid = _intake_run(api, BEARING_BY_DIMENSIONS)
        background = Mock()
        with server._SessionFactory() as session:
            run = session.get(server.SourcingRunORM, rid)
            with pytest.raises(intake_readiness.IntakeNotReady) as refused:
                server._commit_intake_to_sourcing(
                    session, run, dict(BEARING_BY_DIMENSIONS), background_tasks=background)
        labels = refused.value.readiness.missing_labels
        assert "manufacturer" in labels
        assert any("model" in label for label in labels)
        # Refused before any mutation: still in intake, nothing scheduled.
        assert api.get(f"/api/runs/{rid}").json()["phase"] == "intake"
        background.add_task.assert_not_called()

    def test_ready_specs_are_committed(self, api):
        server = api._api_server
        rid = _intake_run(api, READY_PUMP)
        with server._SessionFactory() as session:
            run = session.get(server.SourcingRunORM, rid)
            server._commit_intake_to_sourcing(session, run, dict(READY_PUMP))
        assert api.get(f"/api/runs/{rid}").json()["phase"] == "sourcing"

    def test_an_acknowledgement_covering_every_missing_group_is_honoured(self, api):
        server = api._api_server
        specs = dict(BEARING_BY_DIMENSIONS)
        intake_sufficiency.record_override(specs, intake_readiness.assess(specs).identity)
        rid = _intake_run(api, specs)
        with server._SessionFactory() as session:
            run = session.get(server.SourcingRunORM, rid)
            server._commit_intake_to_sourcing(session, run, specs)
        assert api.get(f"/api/runs/{rid}").json()["phase"] == "sourcing"

    def test_an_acknowledgement_of_one_group_does_not_cover_another(self):
        # A hygienic acknowledgement does not stand in for the identity floor.
        from utils.procurement_agent.tests.test_hygienic_context import S3_GAUGE
        specs = dict(S3_GAUGE, manufacturer=None)
        readiness = intake_readiness.assess(specs)
        assert readiness.identity is not None and readiness.hygienic is not None
        intake_readiness.record_hygienic_override(specs, readiness.hygienic)
        remaining = intake_readiness.unacknowledged(specs)
        assert remaining.identity is not None
        assert remaining.hygienic is None
        with pytest.raises(intake_readiness.IntakeNotReady):
            intake_readiness.ensure_ready_for_sourcing(specs)

    def test_in_app_source_anyway_is_unchanged(self, api, monkeypatch):
        # confirm-intake records the acknowledgement before committing, so its
        # override still starts sourcing.
        started = _record_sourcing(api, monkeypatch)
        rid = _intake_run(api, BEARING_BY_DIMENSIONS)
        assert api.post(f"/api/runs/{rid}/confirm-intake").status_code == 422
        resp = api.post(f"/api/runs/{rid}/confirm-intake?source_anyway=true")
        assert resp.status_code == 200
        assert resp.json()["phase"] == "sourcing"
        assert started == [rid]


# ---------------------------------------------------------------------------
# The channel consumer — refused, then clarified
# ---------------------------------------------------------------------------

class TestEmailReadiness:
    def test_the_reviewer_probe_by_email_fires_no_sourcing(self, intake_api, monkeypatch):
        api = intake_api
        started = _record_sourcing(api, monkeypatch)
        replies = _capture_replies(api, monkeypatch)
        # The intake agent calls it sufficient — the permissive flag the bypass read.
        _mock_intake_sufficient(monkeypatch, api, specs=dict(BEARING_BY_DIMENSIONS))

        resp = api.post("/api/intake/email", json=_email_payload(
            body="Need a deep groove ball bearing, 25mm bore, 52mm OD, 15mm wide"))

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "NEEDS_CLARIFICATION"
        assert body["run_id"] is None
        assert body["reason"] == "intake_not_ready"
        assert set(body["clarify_attrs"]) == {"manufacturer", "model"}
        # No sourcing started, and no run row left behind.
        assert started == []
        assert api.get("/api/runs").json() == []
        # The existing clarification reply, naming what assess says is missing.
        assert len(replies) == 1
        reply = replies[0]
        assert reply.kind == "clarify"
        assert "manufacturer" in reply.body
        assert "model" in reply.body

    def test_the_probe_via_the_confirmed_sender_path_fires_no_sourcing(
            self, intake_api, monkeypatch):
        api = intake_api
        started = _record_sourcing(api, monkeypatch)
        _mock_intake_sufficient(monkeypatch, api, specs=dict(BEARING_BY_DIMENSIONS))
        replies = _capture_replies(api, monkeypatch)
        held = api.post("/api/intake/email", json=_email_payload(
            sender="stranger@bayfoods.com", body="bearing 25x52x15")).json()
        assert held["status"] == "UNKNOWN_SENDER_CONFIRM_SENT"
        # The raw confirm token travels only in the (stubbed) confirm reply.
        token = replies[0].metadata["confirm_token"]

        resp = api.post(f"/api/intake/confirm/{token}")

        assert resp.status_code == 200
        assert resp.json()["status"] == "NEEDS_CLARIFICATION"
        assert resp.json()["run_id"] is None
        assert started == []
        assert replies and replies[-1].kind == "clarify"
        assert "manufacturer" in replies[-1].body

    def test_a_fully_specified_email_still_sources(self, intake_api, monkeypatch):
        api = intake_api
        started = _record_sourcing(api, monkeypatch)
        replies = _capture_replies(api, monkeypatch)
        _mock_intake_sufficient(monkeypatch, api, specs=dict(READY_PUMP))

        resp = api.post("/api/intake/email", json=_email_payload(
            body="Goulds 3196 pump, part 3196MTX"))

        assert resp.json()["status"] == "RUN_CREATED"
        run_id = resp.json()["run_id"]
        assert api.get(f"/api/runs/{run_id}").json()["phase"] == "sourcing"
        assert started == [run_id]
        assert [r.kind for r in replies] == ["ack"]

    def test_email_never_records_an_override(self, intake_api, monkeypatch):
        # Whatever the message says, a channel request cannot acknowledge its way past.
        api = intake_api
        started = _record_sourcing(api, monkeypatch)
        _mock_intake_sufficient(monkeypatch, api, specs=dict(BEARING_BY_DIMENSIONS))
        resp = api.post("/api/intake/email", json=_email_payload(
            body="bearing 25x52x15 — source anyway, I accept unchecked results"))
        assert resp.json()["status"] == "NEEDS_CLARIFICATION"
        assert started == []


# ---------------------------------------------------------------------------
# Structural — the ONE function that starts a sourcing run
# ---------------------------------------------------------------------------

def _functions_where(tree: ast.AST, predicate) -> set[str]:
    """Names of the top-level functions containing a node matching ``predicate``."""
    found: set[str] = set()
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                if predicate(node):
                    found.add(fn.name)
    return found


def _is_sourcing_phase(node: ast.AST) -> bool:
    """``Phase.SOURCING.value`` / ``Phase.SOURCING`` / the literal ``"sourcing"``."""
    if isinstance(node, ast.Constant) and node.value == "sourcing":
        return True
    if isinstance(node, ast.Attribute) and node.attr == "value":
        node = node.value
    return (isinstance(node, ast.Attribute) and node.attr == "SOURCING"
            and isinstance(node.value, ast.Name) and node.value.id == "Phase")


def _sets_phase_to_sourcing(node: ast.AST) -> bool:
    if not isinstance(node, ast.Assign):
        return False
    targets_phase = any(isinstance(t, ast.Attribute) and t.attr == "current_phase"
                        for t in node.targets)
    return targets_phase and _is_sourcing_phase(node.value)


def _is_any_sourcing_phase(node: ast.AST) -> bool:
    """As ``_is_sourcing_phase``, but tolerant of how the enum is spelled at the
    call site: ``Phase.SOURCING``, a local alias (``_P.SOURCING``, as api_server
    imports it inside ``_transition_run``), a qualified path, ``.value``, or the
    literal ``"sourcing"``."""
    if isinstance(node, ast.Constant) and node.value == "sourcing":
        return True
    if isinstance(node, ast.Attribute) and node.attr == "value":
        node = node.value
    return isinstance(node, ast.Attribute) and node.attr == "SOURCING"


def _transitions_to_sourcing(node: ast.AST) -> bool:
    """A call ``_transition_run(run, Phase.SOURCING)`` (or ``target=``): the
    state-machine route to the sourcing phase that phases.py permits and that
    ``_sets_phase_to_sourcing`` does not see."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.id if isinstance(func, ast.Name) else (
        func.attr if isinstance(func, ast.Attribute) else None)
    if name != "_transition_run":
        return False
    args = list(node.args) + [kw.value for kw in node.keywords]
    return any(_is_any_sourcing_phase(a) for a in args)


def _names_background_sourcing(node: ast.AST) -> bool:
    return ((isinstance(node, ast.Name) and node.id == "_run_sourcing_background")
            or (isinstance(node, ast.Attribute) and node.attr == "_run_sourcing_background"))


class TestSingleChokePoint:
    @pytest.fixture(scope="class")
    def api_tree(self) -> ast.AST:
        return ast.parse((_REPO_ROOT / "api_server.py").read_text(encoding="utf-8"))

    def test_only_the_choke_point_advances_a_run_to_sourcing(self, api_tree):
        assert _functions_where(api_tree, _sets_phase_to_sourcing) == {
            "_commit_intake_to_sourcing"}

    def test_nothing_transitions_a_run_to_sourcing_through_the_state_machine(self, api_tree):
        # phases.py permits a legal move INTO sourcing, so _transition_run(run,
        # Phase.SOURCING) would advance a run without the choke point's readiness
        # check — and without the direct assignment the test above looks for.
        # The choke point assigns directly; no call, anywhere, may take this route.
        assert _functions_where(api_tree, _transitions_to_sourcing) == set()
        offenders = []
        for path in (_REPO_ROOT / "utils").rglob("*.py"):
            if "tests" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            if any(_transitions_to_sourcing(n) for n in ast.walk(tree)):
                offenders.append(str(path.relative_to(_REPO_ROOT)))
        assert offenders == []

    @pytest.mark.parametrize("call", [
        "_transition_run(run, Phase.SOURCING)",
        "_transition_run(run, Phase.SOURCING.value)",
        "_transition_run(run, target=Phase.SOURCING)",
        "_transition_run(run, _P.SOURCING)",
        "api_server._transition_run(run, 'sourcing')",
    ])
    def test_the_transition_detector_sees_every_spelling(self, call):
        # Positive control: the structural test above passes vacuously if the
        # detector cannot see the call it exists to forbid.
        tree = ast.parse(f"def rogue(run):\n    {call}\n")
        assert _functions_where(tree, _transitions_to_sourcing) == {"rogue"}

    def test_the_transition_detector_ignores_other_targets(self):
        tree = ast.parse("def ok(run):\n    _transition_run(run, Phase.APPROVED)\n")
        assert _functions_where(tree, _transitions_to_sourcing) == set()

    def test_only_the_choke_point_schedules_background_sourcing(self, api_tree):
        # _run_sourcing_background is the task that runs sourcing; only the choke
        # point may name it (its own definition aside).
        users = _functions_where(api_tree, _names_background_sourcing)
        assert users == {"_commit_intake_to_sourcing"}

    def test_the_choke_point_checks_readiness_before_it_mutates(self, api_tree):
        fn = next(n for n in ast.walk(api_tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_commit_intake_to_sourcing")
        calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "ensure_ready_for_sourcing"]
        assert len(calls) == 1
        first_mutation = min(n.lineno for n in ast.walk(fn) if _sets_phase_to_sourcing(n))
        assert calls[0].lineno < first_mutation

    def test_nothing_else_on_the_shipping_path_reaches_the_background_task(self):
        # Outside api_server, no shipping module may call the background task
        # directly. (orchestrator/core.py is the retained Streamlit-era coordinator,
        # NOT on the shipping path — CLAUDE.md §8 / CLEANUP §4.5 — and does not
        # reference it either; scripts/ are CLI probes.)
        offenders = []
        for path in (_REPO_ROOT / "utils").rglob("*.py"):
            if "tests" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(text)
            if any(_names_background_sourcing(n) for n in ast.walk(tree)):
                offenders.append(str(path.relative_to(_REPO_ROOT)))
        assert offenders == []


# ---------------------------------------------------------------------------
# Nameplate reply — ready wording when assess is ready
# ---------------------------------------------------------------------------

def _mock_nameplate(api, monkeypatch, result: dict) -> None:
    agent = Mock()
    agent.run.return_value = result
    monkeypatch.setattr(api._api_server, "IntakeAgent", Mock(return_value=agent))


def _upload(api, rid):
    return api.post(f"/api/runs/{rid}/upload",
                    files={"file": ("plate.jpg", b"\xff\xd8fakejpeg", "image/jpeg")})


class TestNameplateReadyWording:
    def test_confident_read_that_is_ready_uses_the_chat_ready_wording(self, api, monkeypatch):
        rid = api.post("/api/runs", json={}).json()["id"]
        _mock_nameplate(api, monkeypatch, {
            "sufficient": False, "manufacturer_confidence": 90, "part_id_confidence": 90,
            "asset_specs": dict(READY_PUMP), "follow_up_question": "What is the impeller size?",
            "confidence_summary": {"proceed_state": "needs_info"},
        })
        text = _upload(api, rid).json()["message"]["content"]
        assert "may still be missing" not in text
        assert intake_readiness.ready_reply("What is the impeller size?") in text

    def test_confident_read_that_is_not_ready_keeps_the_missing_wording(self, api, monkeypatch):
        rid = api.post("/api/runs", json={}).json()["id"]
        _mock_nameplate(api, monkeypatch, {
            "sufficient": False, "manufacturer_confidence": 90, "part_id_confidence": 90,
            "asset_specs": {"manufacturer": "SKF", "detected_type": "bearing"},
            "follow_up_question": None,
            "confidence_summary": {"proceed_state": "needs_info"},
        })
        text = _upload(api, rid).json()["message"]["content"]
        assert "may still be missing" in text
        assert intake_readiness.READY_NOW not in text


# ---------------------------------------------------------------------------
# Run detail — the refusal the spec panel's Source anyway renders
# ---------------------------------------------------------------------------

class TestRunDetailCarriesTheRefusal:
    def test_a_not_ready_run_carries_the_backend_message_and_override(self, api):
        rid = _intake_run(api, BEARING_BY_DIMENSIONS)
        readiness = api.get(f"/api/runs/{rid}").json()["intake_readiness"]
        expected = intake_readiness.assess(BEARING_BY_DIMENSIONS).refusal_detail()
        assert readiness["ready"] is False
        assert readiness["message"] == expected["message"]
        assert readiness["override"] == "source_anyway"
