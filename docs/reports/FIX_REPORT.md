# FIX REPORT — Order-dependent flake in test_api_server.py / test_run_capture.py

Branch: `test/flag-on-integration`. Two commits (one per file / root-cause), **not pushed**.
Test-isolation fix only — **no production code changed.**

> This file is **uncommitted** (per the brief: a report for review, not a tracked
> artifact). It replaces a leftover `FIX_REPORT.md` from a prior, unrelated session
> (`fix/intake-anchored-clarification`) — that report's content lives in its own
> branch's commits, not here.

---

## The flake

`test_api_server.py::TestStaticEndpoints::test_health` asserts an exact-equality
dict against `/api/health`:

```python
assert resp.json() == {"status": "ok", "version": "1.0.0-phase1",
                       "demo_mode": False, "capture_failures": 0, "label_failures": 0}
```

Three of those fields (`demo_mode`, `capture_failures`, `label_failures`) are read
by the health handler (`api_server.py:2998-3016`) from **process-global module
attributes / counters** that other tests legitimately mutate and don't always
restore. So `test_health` passes in isolation but fails when a polluter ran
earlier in the session. In alphabetical collection order `test_api_server.py`
runs **before** the polluter files, so the full suite is green — the flake only
surfaces under reordering (`--lf`, reverse, or a polluter sorted earlier). That
is exactly the "passes in isolation / 3 clean runs then fails" signature
reported.

## Reproduction (recorded before the fix)

Four distinct orderings, each reproducing a different field mismatch:

| # | Command (cwd = repo root) | Failure |
|---|---------------------------|---------|
| RC#1 | `RUN_CAPTURE=1 uv run pytest utils/procurement_agent/tests/test_run_capture.py::TestFailSoft::test_forced_write_failure_does_not_raise_and_increments_counter utils/procurement_agent/tests/test_api_server.py::TestStaticEndpoints::test_health -q` | `{'capture_failures': 2} != {'capture_failures': 0}` |
| RC#1b | `RUN_CAPTURE=1 uv run pytest utils/procurement_agent/tests/test_run_labels.py::TestFailSoft::test_read_failure_is_fail_soft utils/procurement_agent/tests/test_api_server.py::TestStaticEndpoints::test_health -q` | `{'label_failures': 4} != {'label_failures': 0}` |
| RC#2 | `RUN_CAPTURE=1 uv run pytest utils/procurement_agent/tests/test_demo_mode.py utils/procurement_agent/tests/test_api_server.py::TestStaticEndpoints::test_health -q` | `{'demo_mode': True} != {'demo_mode': False}` |
| RC#3 | `uv run pytest utils/procurement_agent/tests/test_run_capture.py utils/procurement_agent/tests/test_api_server.py::TestStaticEndpoints::test_health -q` (no env prefix) | right contains 2 more items: `capture_failures`, `label_failures` (keys missing) |

## Root cause

**Shared module-level state not reset between tests** — the pattern flagged in
the brief.

- **`utils/run_capture.py:116` `_capture_failures`** (and the symmetric
  `utils/run_labels.py:118` `_label_failures`) are module-level accumulators.
  The fail-soft tests that deliberately force write failures increment them;
  the per-test `cap` / `cap_off` / `labels` fixtures call `reset_failures()` at
  **setup only, not teardown**. So a forced-failure test that is the last to
  touch the counter leaves it non-zero for the rest of the session.
  → **RC#1 / RC#1b.**

- **`api_server.py:84` `DEMO_MODE`** is read once at import.
  `test_demo_mode.py::_import_api_server_fresh` does `importlib.reload(api_server)`
  under `DEMO_MODE=true` (lines 59 / 85), leaving `api_server.DEMO_MODE = True`
  for the rest of the session. The `api` fixture in `test_api_server.py` did
  **not** force it back (unlike `test_run_capture_live.py`'s `api` fixture,
  which already does at line 69). → **RC#2.**

- **`utils/run_capture.py:61` (and `run_labels.py:70`) `RUN_CAPTURE`** is read
  once at import. `.env` is loaded by `api_server.load_dotenv()`
  (`api_server.py:21`) — **not by pytest itself** (no pytest-dotenv plugin; the
  conftest comment claiming "pytest loads .env" is wrong). So if `run_capture`
  is imported earlier in the session without `api_server` (e.g.
  `test_run_capture` collected/run first, in a plain `uv run pytest` with no
  shell `RUN_CAPTURE`), the flag bound at import is `False` and stays `False`
  (module cached in `sys.modules`); `/api/health` then omits the
  `capture_failures` / `label_failures` keys entirely. → **RC#3** (latent under
  plain `uv run pytest`; masked under the env-prefix verification because the
  shell sets `RUN_CAPTURE=1`).

The polluter named in scope is `test_run_capture.py` (RC#1). RC#1b / RC#2
originate in sibling files (`test_run_labels.py`, `test_demo_mode.py`) but the
**victim** in every case is `test_api_server.py::test_health`, so the robust fix
isolates the victim too.

## What changed

### Commit 1 — `test_run_capture.py` (the polluter; root cause: counter leak)

Added a module-level **autouse** fixture that calls `run_capture.reset_failures()`
at **teardown**, so no test in this file can leak a non-zero
`_capture_failures` into the session regardless of order. The existing
`cap` / `cap_off` fixtures already reset at setup; this closes the teardown gap.
Mirrors the autouse-isolation idiom from the TIER1_V2 fix
(`test_sourcing_agent.py::_isolate_tier1_catalog_path`, commit `7be667b`) and
the Night 8 isolation fixes.

### Commit 2 — `test_api_server.py` (the victim; root cause: DEMO_MODE attr leak + counter leak + import-order flag)

Hardened the `api` fixture (used by every test in the file, including
`test_health`) to make `test_health` order-independent against **all** polluters,
not just the named one:

1. `monkeypatch.setattr(api_server, "DEMO_MODE", False)` — mirrors the existing
   guard in `test_run_capture_live.py`'s `api` fixture (line 69). No-op in the
   normal case (the plain app is built DEMO_MODE-off); only protects against the
   leaked-True case from `test_demo_mode`'s reload. → fixes RC#2.
2. `monkeypatch.setattr(run_capture, "RUN_CAPTURE", True)` and the same for
   `run_labels` — pins the flywheel flags ON so the health contract (the
   `capture_failures` / `label_failures` keys, committed in `3e66c71`) holds
   independent of which module was imported first. → fixes RC#3.
3. `run_capture.reset_failures()` + `run_labels.reset_failures()` — restores the
   fresh-process zero-counter state `test_health` asserts, regardless of which
   polluter ran last. → fixes RC#1 / RC#1b (defence-in-depth alongside commit 1).
4. Redirect `run_capture` / `run_labels` `_DATA_DIR` + `_DB_PATH` to `tmp_path` —
   with the flags now pinned ON, the live request path (create_run, /messages,
   confirm-intake) emits capture rows; pointing them at the throwaway tmp DB
   keeps this file from writing the real `data/run_capture.sqlite` /
   `data/run_labels.sqlite`. Mirrors the `cap_on` / `label_api` fixture
   isolation. No test here reads these stores.

No assertions were changed to match polluted state; no order pinning, sleeps,
xfail, or skip. The fix is isolation (restore clean state before the test).

## Verification

All four recorded reproductions now PASS. Six-file reverse-ordering (every
polluter run before the victim) passes, both with and without the env prefix.

Full suite, multiple runs / orderings:

| Run | Command | Result |
|-----|---------|--------|
| 1 | `RUN_CAPTURE=1 INTAKE_TYPE_AWARE=1 SCORING_V2=1 uv run pytest -q` | **1923 passed, 73 skipped** |
| 2 | `RUN_CAPTURE=1 INTAKE_TYPE_AWARE=1 SCORING_V2=1 uv run pytest -q` | **1923 passed, 73 skipped** |
| 3 | `uv run pytest -q` (plain — the CLAUDE.md §4 way, no env prefix) | **1923 passed, 73 skipped** |
| 4 | rest-of-suite, then `test_run_capture.py test_api_server.py` appended last (polluters-then-victim, plain env) | 1713 + 210 = **1923 passed, 73 skipped** |

The flake is gone, not just quiet: the polluters-then-victim orderings that
previously failed now pass, and plain `uv run pytest` (which previously could
flake via RC#3) is green.

## Out of scope (noted, not fixed here)

- The same reset-at-setup-only counter pattern exists in `test_run_labels.py`,
  `test_run_capture_live.py`, and `test_labeling_api.py`. Their own health/victim
  tests (`test_run_capture_live.py::test_health_flag_on_includes_capture_failures`,
  which asserts `capture_failures == 0`) remain theoretically reachable by a
  counter leak from a sibling file under a reverse ordering. They were green
  across all runs here, and the commit-2 victim fix already protects
  `test_api_server::test_health` from them. A matching autouse teardown reset in
  those three files would close the symmetric gap fully; left as a follow-up to
  keep this change scoped to the two named files.
- The conftest comment "pytest loads .env" is inaccurate (`.env` is loaded by
  `api_server.load_dotenv()` at import, not by pytest). Not corrected here.
- No push. Stopped for review.
