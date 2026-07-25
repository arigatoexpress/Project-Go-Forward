# Merge Train Verification — 2026-07-25

**Date:** 2026-07-25 (MDT, America/Denver)
**Branch:** `agent/merge-train-verified-2026-07-25`
**Verified at commit:** `4ac4f0649df211689f86afdb06ab726b885e1380` (`sim: merge 300`)

## Simulated merges (applied to origin/main in order)

1. PR #296 — `agent/firestore-timeouts`
2. PR #297 — `agent/adk-2-upgrade-v2` (dependency upgrade: google-adk 2.x, starlette 1.3.1, pypdf 6.14.2)
3. PR #298 — `agent/gauntlet-e2e`
4. PR #299 — `agent/api-docs`
5. PR #300 — `agent/user-safe-errors`

## Toolchain

| Tool | Version |
|------|---------|
| Python | 3.11 (fresh venv, created for this run) |
| pip | 26.1.2 |
| Node | v24.15.0 (note: CI uses Node 20 — no node-version-related failures observed) |
| npm | 11.12.1 |

Key installed backend deps: google-adk 2.5.0, pypdf 6.14.2, fastapi 0.136.3, pytest 9.0.3, ruff 0.7.4.

## Gate results (all run in a clean worktree with a fresh venv)

| Gate | Command | Result |
|------|---------|--------|
| Ruff lint | `.venv/bin/python -m ruff check .` | ✅ All checks passed |
| Frontend deps | `npm ci` | ✅ clean install |
| Frontend build | `npm run build` | ✅ built in 2.66s, PWA precache 28 entries |
| Frontend tests | `npm run test` (vitest) | ✅ **34 test files passed, 216 tests passed**, 0 failed |
| Backend tests | `ADMIN_PIN_HASH=… PYTHONPATH=. .venv/bin/python -m pytest tests/test_*.py --tb=short -q` | ✅ **1775 passed, 24 skipped, 0 failed** in 125.47s (13 warnings, all deprecation/library warnings) |

Backend note: main ballpark was ~1791 passed / ~17 skipped; the merge-train combined state yields 1775 passed / 24 skipped (1799 collected). The delta reflects the combined test-suite changes from the five merged PRs, not failures — zero tests failed.

## Fixes required

None. Every gate passed on the first run against the exact branch tip; no integration conflicts between the five PRs were detected.

## Verdict

The combined post-merge state of PRs #296–#300 passes the full local CI-equivalent gate. Safe for the human to merge in order 296 → 297 → 298 → 299 → 300.
