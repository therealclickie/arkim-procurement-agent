"""PH-01 round 3b — the run detail carries the ONE readiness decision.

CLEANUP 5.7: the intake card decided "Part identified" on its own (manufacturer +
model/PN, or spec_based_sourcing), so it could enable Find options on specs that
confirm-intake refuses. ``RunDetail.intake_readiness`` is now
``intake_readiness.assess`` on the persisted specs, and these tests pin that it
agrees with confirm-intake for the same run.
"""

from utils import hygienic_context, intake_readiness
from utils.procurement_agent.tests.test_hygienic_context import (  # noqa: F401 (fixture)
    PLAIN_GAUGE, S3_GAUGE, _seed, api,
)

#: Clears both gates.
_READY = dict(S3_GAUGE, process_connection="Tri-Clamp", connection_size='1.5"',
              wetted_material="316L", hygienic_certification="3-A")


def _readiness(api, rid):
    resp = api.get(f"/api/runs/{rid}")
    assert resp.status_code == 200, resp.text
    return resp.json()["intake_readiness"]


def _confirm_status(api, rid):
    return api.post(f"/api/runs/{rid}/confirm-intake").status_code


class TestRunDetailReadiness:
    def test_a_model_only_run_is_not_ready_and_names_the_manufacturer(self, api):
        rid = _seed(api, dict(PLAIN_GAUGE, manufacturer=None))
        readiness = _readiness(api, rid)
        assert readiness["ready"] is False
        assert "manufacturer" in readiness["missing_attrs"]
        assert "manufacturer" in readiness["missing_labels"]
        assert _confirm_status(api, rid) == 422

    def test_a_hygienic_run_missing_certification_is_not_ready(self, api):
        rid = _seed(api, dict(_READY, hygienic_certification=None))
        readiness = _readiness(api, rid)
        assert readiness["ready"] is False
        assert readiness["missing_attrs"] == ["hygienic_certification"]
        assert readiness["missing_labels"] == list(
            intake_readiness.assess(dict(_READY, hygienic_certification=None)).missing_labels)
        assert _confirm_status(api, rid) == 422

    def test_a_fully_answered_run_is_ready_and_confirm_proceeds(self, api):
        rid = _seed(api, _READY)
        readiness = _readiness(api, rid)
        assert readiness == {"ready": True, "missing_attrs": [], "missing_labels": []}
        assert _confirm_status(api, rid) == 200

    def test_spec_based_sourcing_alone_does_not_make_a_run_ready(self, api):
        # The old frontend rule treated spec_based_sourcing as ready; the backend
        # decision (and confirm) does not.
        rid = _seed(api, dict(PLAIN_GAUGE, manufacturer=None, model=None,
                              spec_based_sourcing=True))
        assert _readiness(api, rid)["ready"] is False
        assert _confirm_status(api, rid) == 422

    def test_every_missing_item_across_both_gates_is_listed(self, api):
        rid = _seed(api, dict(S3_GAUGE, manufacturer=None))
        readiness = _readiness(api, rid)
        assert readiness["missing_attrs"] == (
            ["manufacturer"] + list(hygienic_context.REQUIRED_FIELDS))
