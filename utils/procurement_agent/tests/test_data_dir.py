"""T10 / ruling R10 (evaluation finding F-04) — data location is configurable.

What the evaluation verified offline
(``eval/e2e-flags-on:eval/e2e/evidence/verify/verify_offline_checks.json`` →
``F-04``): fifteen store modules each computed ``<repo>/data`` for themselves and
``any_env_override`` was ``false``. The harness had to monkeypatch every module
individually to run against isolated stores.

The compatibility contract, and the reason this is safe to land: **with
``GOFER_DATA_DIR`` unset, every path is byte-identical to today.** The module-level
``_DATA_DIR`` / ``_DB_PATH`` attributes stay exactly where they are — only how they
are computed changes — so ``conftest`` and the dozens of
``monkeypatch.setattr(mod, "_DATA_DIR", tmp_path)`` fixtures are untouched.

The override is read at import time, which is what a process-level setting means, so
the "with it set" assertions run in a SUBPROCESS with a clean import graph rather
than reloading modules under the live one.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from utils import data_dir

REPO_ROOT = Path(data_dir.repo_root())

#: The store modules R10 covers: the fifteen the evaluation enumerated, plus the
#: four the gate's J9 added (persistence and the two JSON stores inside utils/,
#: and api_server's seeded handoffs file).
STORE_MODULES = (
    "utils.audit_log", "utils.brand_intelligence", "utils.claim_tokens",
    "utils.intake_channels", "utils.notifications_store", "utils.orders",
    "utils.quote_store", "utils.quote_tokens", "utils.run_capture",
    "utils.run_labels", "utils.send_governance", "utils.site_settings",
    "utils.spec_lookup", "utils.supplier_accounts", "utils.supplier_registry",
)

_PROBE = """
import json, os, sys
out = {}
for name in %(modules)r:
    mod = __import__(name, fromlist=["_DATA_DIR"])
    out[name] = {"data_dir": getattr(mod, "_DATA_DIR", None),
                 "db_path": getattr(mod, "_DB_PATH", None)}
from utils.procurement_agent.state import persistence
out["persistence"] = {"data_dir": persistence._DATA_DIR,
                      "db_path": persistence._DB_PATH}
from utils import known_parts, price_db
out["utils.known_parts"] = {"data_dir": None, "db_path": known_parts._DB_PATH}
from utils import price_db as _pdb
out["utils.price_db"] = {"data_dir": None, "db_path": _pdb._DB_PATH}
print("<<<JSON>>>" + json.dumps(out))
"""


def _probe(env_data_dir=None):
    """Import every store in a clean subprocess and report its resolved paths."""
    env = dict(os.environ)
    env.pop("GOFER_DATA_DIR", None)
    if env_data_dir is not None:
        env["GOFER_DATA_DIR"] = str(env_data_dir)
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE % {"modules": list(STORE_MODULES)}],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr[-2000:]
    payload = proc.stdout.split("<<<JSON>>>", 1)[1].strip()
    return json.loads(payload)


@pytest.fixture(scope="module")
def unset_paths():
    return _probe(None)


@pytest.fixture(scope="module")
def override_paths(tmp_path_factory):
    target = tmp_path_factory.mktemp("gofer_data")
    return str(target), _probe(target)


class TestTheHelper:
    def test_it_defaults_to_the_repo_data_directory(self, monkeypatch):
        monkeypatch.delenv(data_dir.ENV_DATA_DIR, raising=False)
        assert data_dir.data_dir() == str(REPO_ROOT / "data")

    def test_the_env_var_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv(data_dir.ENV_DATA_DIR, str(tmp_path))
        assert data_dir.data_dir() == str(tmp_path)

    def test_a_blank_value_is_not_an_override(self, monkeypatch):
        monkeypatch.setenv(data_dir.ENV_DATA_DIR, "   ")
        assert data_dir.data_dir() == str(REPO_ROOT / "data")

    def test_data_path_joins_under_it(self, monkeypatch, tmp_path):
        monkeypatch.setenv(data_dir.ENV_DATA_DIR, str(tmp_path))
        assert data_dir.data_path("orders.sqlite") == str(tmp_path / "orders.sqlite")

    def test_the_utils_json_stores_do_not_move_when_it_is_unset(self, monkeypatch):
        monkeypatch.delenv(data_dir.ENV_DATA_DIR, raising=False)
        assert data_dir.utils_store_path("price_db.json") == \
            str(REPO_ROOT / "utils" / "price_db.json")

    def test_but_they_are_collected_when_it_is_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv(data_dir.ENV_DATA_DIR, str(tmp_path))
        assert data_dir.utils_store_path("price_db.json") == \
            str(tmp_path / "price_db.json")

    def test_it_is_read_at_call_time(self, monkeypatch, tmp_path):
        monkeypatch.delenv(data_dir.ENV_DATA_DIR, raising=False)
        before = data_dir.data_dir()
        monkeypatch.setenv(data_dir.ENV_DATA_DIR, str(tmp_path))
        assert data_dir.data_dir() != before


class TestUnsetIsIdenticalToToday:
    """The compatibility contract, asserted path by path."""

    def test_every_store_still_resolves_to_repo_data(self, unset_paths):
        expected = str(REPO_ROOT / "data")
        for name in STORE_MODULES:
            assert unset_paths[name]["data_dir"] == expected, name

    def test_persistence_still_resolves_to_repo_data(self, unset_paths):
        assert unset_paths["persistence"]["data_dir"] == str(REPO_ROOT / "data")
        assert unset_paths["persistence"]["db_path"] == \
            str(REPO_ROOT / "data" / "sourcing_runs.sqlite")

    def test_the_two_utils_json_stores_still_live_in_utils(self, unset_paths):
        assert unset_paths["utils.known_parts"]["db_path"] == \
            str(REPO_ROOT / "utils" / "known_parts.json")
        assert unset_paths["utils.price_db"]["db_path"] == \
            str(REPO_ROOT / "utils" / "price_db.json")

    def test_every_db_path_still_sits_inside_its_own_data_dir(self, unset_paths):
        for name in STORE_MODULES:
            db = unset_paths[name]["db_path"]
            if db:
                assert db.startswith(unset_paths[name]["data_dir"]), name

    def test_conftest_computes_the_same_path(self):
        """conftest builds its own ``_ROOT/data`` — it must still agree."""
        from utils.procurement_agent.tests import conftest
        assert Path(conftest._ROOT).resolve() == REPO_ROOT.resolve()


class TestTheOverrideMovesEverything:
    def test_every_store_writes_under_it(self, override_paths):
        target, paths = override_paths
        for name in STORE_MODULES:
            assert paths[name]["data_dir"] == target, name

    def test_including_persistence(self, override_paths):
        target, paths = override_paths
        assert paths["persistence"]["data_dir"] == target
        assert paths["persistence"]["db_path"].startswith(target)

    def test_including_the_two_utils_json_stores(self, override_paths):
        target, paths = override_paths
        assert paths["utils.known_parts"]["db_path"].startswith(target)
        assert paths["utils.price_db"]["db_path"].startswith(target)

    def test_nothing_is_left_pointing_at_the_repo(self, override_paths):
        target, paths = override_paths
        repo_data = str(REPO_ROOT / "data")
        for name, entry in paths.items():
            for key, value in entry.items():
                if value:
                    assert not value.startswith(repo_data), f"{name}.{key} = {value}"


class TestNoModuleHardCodesTheDataDirectory:
    """A source scan — the thing that would catch the NEXT module to forget."""

    #: The pre-R10 idiom, in every form the repo used it.
    _HARDCODED = (
        re.compile(r'os\.path\.join\(\s*os\.path\.dirname\(os\.path\.dirname\('
                   r'__file__\)\)\s*,\s*["\']data["\']\s*\)'),
        re.compile(r'os\.path\.dirname\(__file__\)\s*,\s*["\']data["\']'),
    )

    def _python_sources(self):
        for path in list((REPO_ROOT / "utils").rglob("*.py")) + [REPO_ROOT / "api_server.py"]:
            if "__pycache__" in path.parts or "/tests/" in path.as_posix():
                continue
            if path.name == "data_dir.py":
                continue          # the one place allowed to know the layout
            yield path

    def test_no_module_builds_the_data_path_itself(self):
        offenders = []
        for path in self._python_sources():
            text = path.read_text(encoding="utf-8", errors="ignore")
            for pattern in self._HARDCODED:
                if pattern.search(text):
                    offenders.append(str(path.relative_to(REPO_ROOT)))
                    break
        assert offenders == [], (
            "these modules hard-code <repo>/data instead of using "
            f"utils.data_dir: {offenders}")

    def test_the_scan_would_catch_the_old_idiom(self):
        """Proves the scan has teeth against the exact string F-04 was about."""
        sample = ('_DATA_DIR = os.path.join(os.path.dirname('
                  'os.path.dirname(__file__)), "data")')
        assert any(p.search(sample) for p in self._HARDCODED)

    def test_every_store_module_imports_the_helper(self):
        for name in STORE_MODULES:
            path = REPO_ROOT / (name.replace(".", os.sep) + ".py")
            text = path.read_text(encoding="utf-8")
            assert "data_dir" in text, name
