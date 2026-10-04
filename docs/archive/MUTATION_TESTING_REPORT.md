# Mutation Testing Reports

## Frontend — 2026-10-01 (latest)

Deep mutation testing of the Next.js frontend (`frontend/src`), run
completely fresh: artifacts wiped, manifest regenerated (9,944 mutants over
126 files), coverage map rebuilt per test file (293 files, zero failures),
every mutant executed with escalating selections, a fix cycle that added 11
test files (35 tests, 93 verified kills), Phase B full-selection
re-verification of every survivor, and a same-day follow-up round that
killed 103 more fixable survivors. The methodology is preserved in the
[frontend campaign](frontend-mutation/CAMPAIGN.md); its generated per-mutant
report was historical output and is no longer tracked.

| Metric | Value |
|---|---|
| Mutants (fresh manifest) | 9,944 (100% of manifest) |
| Killed by tests | 7,972 |
| Killed by timeout (confirmed hang) | 2 (api-client 10 s abort budget; image-deps comparator loop) |
| Survived (Phase B re-verified) | 1,892 (of which app pages 1,646) |
| Not covered by any test | 78 |
| **Mutation score (covered code)** | **80.8%** |

Baseline suite: 4,965 tests / 293 files, green after fixing seven
pre-existing test bugs (five planner date/timezone flakes, two heavy-test
budgets). Closing suite: 5,026 tests / 313 files.

(2026-10-01 audit, 10-1: resynced)

## Backend — 2026-09-30

Deep mutation testing of the FastAPI backend (`backend/app`), run completely
fresh: recreated venv, regenerated manifest from current source, full-suite
coverage-contexts baseline, 6,565-mutant campaign, full-selection
re-verification, and a gap-fix cycle that added 68 tests across 15 new test files — then
re-measured every non-killed mutant against the strengthened suite.

### Headline

| Metric | Campaign (fresh) | After gap fixes |
|---|---|---|
| Mutants | 6,565 (100% of manifest) | same |
| Killed | 5,819 + 14 timeout | **6,043 + 5 confirmed hangs** |
| Survived (full-selection verified) | 281 | **74** |
| Not covered by any test | 451 | 443 |
| **Mutation score (covered code)** | **95.4%** | **98.8%** (98.79%) |

Baseline suite: 4,836 passed / 4 skipped, 92.52% line+branch coverage
(pytest-cov, per-test contexts). Final suite: 4,904 passed / 4 skipped
(4,836 previous + 68 new tests).

## Method

1. **Fresh environment**: `.venv` recreated via `uv sync --frozen --extra
   dev`; all prior artifacts (`manifest.json`, `results.jsonl`, logs,
   `.coverage-mut`) deleted; Postgres `max_connections` raised to 300 for
   10 parallel workers.
2. **Baseline**: `pytest --cov=app --cov-context=test` (40 min) → per-test
   coverage contexts for selection.
3. **Campaign**: `mutation/mutate_gen.py` regenerated the manifest from
   current source (6,565 mutants; same operator set as 2026-09-22), then
   `mutation/mutate_run.py --workers 10` executed every mutant with
   escalating 15→60-test selections (103 min, 0 runner errors, tree
   byte-identical afterwards).
4. **Full-selection re-verification**: every SURVIVED verdict re-run against
   its complete covering selection (cap 400): 36 of 297 were sampling
   artifacts → 281 confirmed survivors.
5. **Gap fixes**: 68 new tests in 15 files (see below) pinning the
   surviving behavior: frozen settings defaults, exact age/curve math,
   rate-limiter sweep/LRU semantics, notification retry/digest/truncation,
   JWT keypair hygiene, TOTP RFC 4226 vectors, tenant-scoped chronology
   probes, lifecycle-move attribution, retention boundaries, feed-shift
   largest-remainder, kid-tag collision fallback, provider transport
   contract, and security module constants.
6. **Verification**: contexts re-collected with the new tests; all 732
   non-killed mutants re-measured; a final pass force-included the 16 gap
   files in every selection (coverage-guided sampling can miss a killing
   test, and module-level constants attribute their import context to one
   arbitrary first-importing test). 207 former survivors are now killed
   (281 → 74; plus 8 not-covered lines now covered and killed).
7. **Edge-verdict verification** (`mutate_verify_extremes.py`): every
   TIMEOUT was re-run at a 1,200 s budget — 9 were slow-but-finite and got
   real KILLED verdicts, 5 re-timed-out and are confirmed true hangs
   (`security.py:374` / `idempotency.py:401` break→continue loops and the
   `cadence.py:177`, `feeding.py:186`, `simulation_calibration.py:474`,
   `tasks.py:323` boundary flips that spin). Every final SURVIVED verdict
   was then re-run against its FULL covering selection (cap 400) plus the
   gap files: 74 confirmed, 1 more sampling miss killed
   (`simulation_calibration.py:245`).

## What the gap tests fixed (206 mutant kills)

- **`app/core/config.py` + `MigrationSettings` + `ScreeningWorkerSettings`**
  — every shipped default is frozen in a literal table
  (`tests/test_settings_defaults.py`): any `Field(default=N)` ±1 or
  `24 * 7 → 24 + 7` drift now fails. Security-relevant defaults (TTLs,
  Argon2 cost, rate limits, body-size caps, screening budgets) included.
- **`services/dashboard.py` / `services/simulation_calibration.py`** — the
  age-in-months arithmetic and the whole interpolation core
  (neighbour selection, span, lerp) pinned to exact values, plus isotonic
  PAVA, rescale band, and fractional-months conventions.
- **`services/notifications/service.py`** — retry attempt budget and log
  format, claim-settle deadline arithmetic (fake loop clock), digest
  fan-out continuing past inactive recipients, feed-reorder message
  truncation/ellipsis boundary, overdue-critical 3-day threshold.
- **`security.py`** — JWT keypair bootstrap: RSA-2048/e=65537 shape, exact
  file modes (0600/0644/0600 lock), legacy key-dir permission repair
  arithmetic incl. sticky-bit preservation, half-published-pair
  completeness (`and`/`or`) with the torn-pair warning contract;
  TOTP/HOTP: RFC 4226 Appendix-D vectors, base32 casefolding, replay
  high-water floor.
- **`ratelimit.py`** — sweep schedule pinned to `now + interval` at exact
  boundaries, sweep expires other farms' stale buckets only when
  scheduled, LRU touch semantics on probe/hit/blocked.
- **`services/chronology.py`** — behavioural multi-tenant/sibling scoping
  for both chronology probes (the old SQL-text grep passes `!=` too).
- **`services/tasks.py`** — task-generated bucket moves keep
  `created_by_id` (quarantine release, gestation day-100, pre-delivery).
- **`services/retention.py`** — strictly-older cutoff boundary + counted,
  farm-scoped crop deletion.
- **screening providers** — `max_tokens: 1024` request pins, text
  extraction skips non-dict/non-text blocks, injected clients never
  closed by `aclose()`, owned clients really are.
- **misc** — health ledger note formatting, feed shift 40/20/40
  largest-remainder gram splits, kid-tag `-A` + 12-hex collision suffix,
  `_browser_origin` default-port normalization, clientless-request IP
  fallback, module constants (`_RUN_BUDGET_*`, IP multiplier,
  `REBREED_AFTER_RESTING_DAYS`).

## The 74 remaining survivors — triage

**Equivalent or defense-in-depth-only (~18):**
- `security.py:363` (×2): `O_RDONLY == 0`, so `&`/`|` swaps are no-ops.
- `security.py:372` (×6): read-bound arithmetic sits behind the
  `st_size > 1 MiB` pre-check; it only binds if a file grows between stat
  and read (TOCTOU layering). Chunk-size ints change loop count only.
- `ratelimit.py:149/298`: `touch=True` shadowed by the immediately
  preceding `_prune` touch (call-order makes the flag a no-op).
- `ratelimit.py:483`: log-only throttle-rejection counter arithmetic.
- `simulation_calibration.py:172`: `a < age` vs `<=` — exact anchors take
  the earlier `in lookup` branch, so equality is unreachable here.
- `notifications/service.py:148`: `max(1, attempts)` clamp with
  `notifications_send_retry_attempts` ge=1 at the settings layer.
- `api/auth.py:2840`: `break→continue` in the recovery-code loop —
  performance only (first matching candidate is overwritten by an equal
  value at most).
- `api/_shared.py:420/424`: `FOR SHARE` read-lock flags — behavioural only
  under concurrent contention (lock-mode hardening, not single-run
  observable).
- `feeding.py:138` (×2): remainder-ranking modulus under the fixed
  40/20/40 split — allocation orderings coincide for every total.

**Fixable, documented as follow-ups (~56):** concentrated in
`simulation_calibration` flow math (24: SQL age arithmetic, gestation
window 90–220 and the 1–7-month clamp, birth-weight filters, mortality
windows, feed/labour sample sizing — each needs an exact-value
`calibrate_farm_assumptions` fixture with directly-seeded
kidding/breeding/transaction rows), dashboard/feeding readiness and creep
boundaries (4), chronology kidding-probe scope (2), tasks weaning/postpartum
paths (4), auth PIN/refresh bookkeeping (6), cadence created-flags (2),
screening pipeline/images/rotation internals (7), and singletons elsewhere.
Full per-mutant diffs with originals: `backend/mutation/report.md`.

Not-covered lines fell 451→443; they cluster in schema constraint literals
(`schemas/team.py`, `schemas/planner.py`) and rarely-hit API branches —
listed in `backend/mutation/report.md` per file.

## Harness hardening (kept for future campaigns)

- `mutate_run.py` now sets `PYTHONDONTWRITEBYTECODE=1` for every worker:
  a byte-exact restore that lands in the same mtime second as a
  same-sized mutant leaves the mutant's `.pyc` looking fresh — observed
  live (the swapped-branches mutant of `record_screening_provider_call`
  outlived its restore and failed `test_metrics` until `__pycache__` was
  purged).
- New `mutate_reverify.py` (full-selection re-verification via the locked
  `Runner.run`), `mutate_rerun_survivors.py` (re-measure non-killed after
  new tests + fresh contexts), `mutate_final_pass.py` (force-include gap
  files in every selection), `mutate_triage.py` (class bucketing).
  Ad-hoc threads must never call `Runner.execute` directly — unlocked
  same-file mutations cross-contaminate verdicts (two such passes were
  discarded and re-run on 2026-09-30).

## Reproduce

```bash
cd backend
.venv/bin/python mutation/mutate_gen.py                        # manifest
COVERAGE_FILE=.coverage-mut .venv/bin/python -m pytest -q --cov=app \
    --cov-context=test --cov-report= -p no:cacheprovider        # contexts
.venv/bin/python mutation/mutate_run.py --workers 10           # campaign
.venv/bin/python mutation/mutate_reverify.py --workers 10      # full-selection
.venv/bin/python mutation/mutate_rerun_survivors.py --workers 10  # after fixes
.venv/bin/python mutation/mutate_final_pass.py --workers 10    # gap-aimed
.venv/bin/python mutation/mutate_verify_extremes.py --status TIMEOUT  # hangs
.venv/bin/python mutation/mutate_verify_extremes.py --status SURVIVED # final check
.venv/bin/python mutation/mutate_report.py                     # report.md
```

Artifacts: `backend/mutation/{manifest.json, results.jsonl, report.md}`
(committed); `*.log` and `.coverage-mut` are transient (gitignored).

## Caveats

- Verdicts use escalating capped selections (15→60); re-verification,
  the gap-aimed final pass, and the edge-verdict pass each ran every
  remaining mutant against its full (cap-400) covering selection plus all
  15 gap files, so no known killing test was left out of any final verdict.
- The 5 remaining timeout-kills were confirmed as true hangs at a 1,200 s
  budget (see step 7); the other 9 original timeouts were slow-but-finite
  and carry real KILLED verdicts.
- The score is a property of this operator set; it complements, not
  replaces, the 92.52% line+branch coverage ratchet.
