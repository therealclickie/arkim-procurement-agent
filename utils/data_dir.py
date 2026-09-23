"""Where the data lives — one helper, one environment variable.

Arc 5 / ruling R10 (evaluation finding F-04). Nineteen modules each computed
``<repo>/data`` for themselves, none read any environment override, and the
evaluation harness had to monkeypatch every one of them individually to run
against isolated stores (``eval/e2e-flags-on:eval/e2e/harness.py``, ``_STORE_MODULES``;
``eval/e2e/evidence/verify/verify_offline_checks.json`` → ``F-04``,
``any_env_override: false``).

``GOFER_DATA_DIR`` now names the directory. **With it unset, every path is
byte-identical to today** — that is the whole compatibility contract, and it is
what lets the existing ``conftest`` and the dozens of
``monkeypatch.setattr(mod, "_DATA_DIR", tmp_path)`` fixtures keep working
untouched. Modules keep their module-level ``_DATA_DIR`` / ``_DB_PATH``
attributes; only how those are COMPUTED changes.

Two store shapes exist, and both stay where they are by default:

* ``data_dir()`` — the sqlite/JSON stores under ``<repo>/data``.
* ``utils_store_path(name)`` — ``known_parts.json`` and ``price_db.json``, which
  have always lived inside ``utils/`` rather than ``data/``. An explicit
  ``GOFER_DATA_DIR`` collects them with everything else; unset, they do not move.
"""

from __future__ import annotations

import os

#: The environment variable that relocates every store.
ENV_DATA_DIR = "GOFER_DATA_DIR"

#: ``<repo>`` — the parent of the ``utils`` package this module lives in.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The default, and the only path any of this produced before R10.
_DEFAULT_DATA_DIR = os.path.join(_REPO_ROOT, "data")


def _override() -> str:
    return (os.environ.get(ENV_DATA_DIR) or "").strip()


def repo_root() -> str:
    """The repository root, resolved from this module's own location."""
    return _REPO_ROOT


def data_dir() -> str:
    """The directory the stores live in: ``$GOFER_DATA_DIR``, else ``<repo>/data``.

    Read at call time, so a process that sets the variable before importing a
    store gets it. Nothing is created here — each store still makes its own
    directory when it first connects.
    """
    return _override() or _DEFAULT_DATA_DIR


def data_path(*parts: str) -> str:
    """A path inside :func:`data_dir`."""
    return os.path.join(data_dir(), *parts)


def utils_store_path(name: str) -> str:
    """Where a JSON store that historically lived inside ``utils/`` belongs.

    ``$GOFER_DATA_DIR/<name>`` when the variable is set; otherwise
    ``<repo>/utils/<name>``, exactly as before — an unset variable moves nothing.
    """
    override = _override()
    if override:
        return os.path.join(override, name)
    return os.path.join(_REPO_ROOT, "utils", name)
