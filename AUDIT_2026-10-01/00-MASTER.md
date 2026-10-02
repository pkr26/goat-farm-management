# Independent Multi-Angle Deep Audit — Master Report (2026-10-01)

## 1. Methodology

Ten fully independent auditors examined the codebase in parallel, each with a
disjoint primary scope and a distinct professional lens. Independence was
enforced structurally: no auditor read another auditor's output, the prior
audit reports (`AUDIT_REPORT_2026-09-28.md`, `AUDIT_AND_IMPLEMENTATION_PLAYBOOK.md`),
or anything written during this campaign; each owned 100%-coverage manifests
for its files; findings had to cite `file:line` evidence verified by tracing
callers/callees before reporting.

Every auditor was instructed to read every in-scope file **completely,
line-by-line** (chunking files >2,000 lines). The one deliberate exception:
`frontend/src/api/` (408 orval-generated files) was contract-verified by
track 04 (25-endpoint freshness spot-check, zero drift) and usage-audited by
track 07, rather than line-read — generated code is audited by verifying the
generator contract, not by reading the output.

| # | Track | Report | Verdict |
|---|-------|--------|---------|
| 01 | Security & access control (OWASP) | `01-security-access-control.md` | **Pass** — no exploitable flaw; hardening notes only |
| 02 | Livestock domain logic (animals/breeding/kidding/health/screening) | `02-livestock-domain-logic.md` | **Solid** — state machine discipline exemplary; 2 edge gaps |
| 03 | Operations domain logic (feeding/finance/purchases/tasks/planner/buckets/dashboard/notifications/retention/idempotency) | `03-operations-domain-logic.md` | **Solid with targeted gaps** — 1 High in notifications fan-out |
| 04 | Data layer, migrations & API contract | `04-data-layer-contract.md` | **Pass** — migration chain & contract exact; minor observations |
| 05 | Frontend livestock journeys + auth/worker shells | `05-frontend-livestock-journeys.md` | **Excellent** — race-hardening is the codebase's strongest muscle |
| 06 | Frontend ops journeys + component library | `06-frontend-ops-components.md` | **Strong** — no Critical/High; label/i18n defects + consistency nits |
| 07 | Offline-first & client data layer (distributed-systems lens) | `07-offline-client-data-layer.md` | **Strong** — 1 High policy contradiction at session boundary |
| 08 | Simulation engine & concurrency/numerics | `08-simulation-concurrency.md` | **Strong** — numerics verified by execution; 2 Highs |
| 09 | Infrastructure, Docker, CI/CD & supply chain | `09-infrastructure-supply-chain.md` | **Deploy-ready** — no exploitable deployment flaw |
| 10 | Test integrity & quality-claims verification | `10-test-integrity.md` | **Claims materially true** — all headline numbers reproduce |

### Consolidated severity matrix

| Severity | Count | Tracks |
|----------|-------|--------|
| Critical | **0** | — |
| High | **4** | 03 (notifications), 07 (offline queue), 08 ×2 (simulation) |
| Medium | 20 | 02 ×2, 03 ×2, 04 ×2, 05 ×3, 06 ×2, 07 ×3, 08 ×5, 09 ×1 |
| Low | 42 | all tracks except 01… see per-track reports |
| Info / positive | 42 | all tracks |

**Scale audited:** backend app 372 Python files (~46k app LOC + ~110k test LOC),
frontend 881 TS/TSX files (~32k page + ~12k lib + ~6k component + ~29k generated LOC,
~92k co-located test LOC), 96-file migration chain, `shared/openapi.json` (118 ops),
all Docker/compose/nginx/CI/env files, and both mutation harnesses.

### Lead-auditor verification (this consolidation pass)

Independently of the ten auditors, I re-verified against source:

- **All four High findings** — confirmed, evidence below.
- **Five sampled Mediums across five tracks** (02 lifecycle gap, 03 insurance
  duty, 05 worker `randomUUID`, 04 ×2) — all confirmed; zero false positives
  in the entire sample.
- **Mutation-claims recompute** (frontend): last-line-per-id semantics over
  `frontend/mutation/results.jsonl` → 9,944 unique mutants = 7,972 KILLED +
  2 TIMEOUT + 1,892 SURVIVED + 78 NO_COVERAGE → **80.82%**, matching
  `CAMPAIGN.md`'s 80.8% exactly; `survivors.json` holds exactly 1,892 entries.

## 2. The four High findings (all verified)

### H1 — Same-day alerts keep SMSing deactivated/removed workers
`backend/app/services/notifications/service.py:525-533` (vs the fixed digest
path at `:343-356`)

`notify_alert_class` selects `NotificationRecipient` rows by `farm_id` + opt-in
column only. The membership-activeness fix from the 2026-09-29 audit
(`FarmMembership.is_active` / `User.deleted_at`, service.py:350-351) was applied
to the digest path but **not** to the alert path; `send_notification` sends
straight to `recipient.phone` with no revalidation. Deactivating a worker
(`api/team.py:1205-1211` flips `is_active` only; the FK CASCADE fires solely on
hard delete) leaves the recipient row opted in.

**Impact:** paid SMS spend on former staff + operational/clinical disclosure
(MOVEMENT_RESTRICTION, SCREENING_FLAG, KIDDING_WATCH, OVERDUE_CRITICAL,
FEED_REORDER) to people no longer entitled to it. Deterministic — reached via
`emit_alert` in the animals/health/screening routers and the background sweeps.
**Fix:** apply the same `is_active`/`deleted_at` join filter in
`notify_alert_class` (or filter in `send_notification` so every caller inherits it).

### H2 — Forced logout silently wipes the offline queue mid-drain
`frontend/src/lib/auth-context.tsx:278` ← `frontend/src/lib/api-client.ts:835-842`
← `frontend/src/lib/offline-queue.ts:290,311-322,340-343`

The queue deliberately **keeps** records on 401 ("a later drain after
re-login … can still deliver it", offline-queue.ts:311-317). But the drain
replays through `apiFetch`, whose rejected-refresh path calls
`onAuthFailure` → `clearSession`, whose shared-tablet hygiene step
`wipeOfflineQueue()` (auth-context.tsx:278) empties storage **before** the
drain's merge read (offline-queue.ts:340) — so the "kept" record is written
back to an empty store: `remaining: 0`, `rejected: 0`, no toast, no dialog.
Precondition: a dead refresh session (owner password reset, >14-day offline
stretch) at the moment a drain runs. **Fix:** make `clearSession`'s wipe
explicit-signOut-only, or have the drain hold a session-scoped snapshot guard
that re-persists kept records after teardown.

### H3 — Monte Carlo reports `prob_dscr_below_one = 0.0` when DSCR is unmeasurable
`backend/app/simulation/montecarlo.py:438,478`; schema root cause
`backend/app/simulation/results.py:260`

The deterministic report honestly reports `min_dscr: None` when there are no
repaying years (results.py:155), but the MC counter
`weak_dscr_runs += int(core.min_dscr is not None and core.min_dscr < 1.0)`
treats "unmeasurable" as "not weak", and `prob_dscr_below_one: float` cannot
represent null at all. Example verified by auditor 08: a 12-month moratorium
under a 12-month horizon (EBITDA −₹366,850 vs ₹182,997 interest) still reports
**0% probability of weak DSCR** — false confidence in a deeply cash-negative
scenario used for lending conversations. **Fix:** make
`prob_dscr_below_one: float | None` and propagate `None` when no run had a
measurable DSCR (mirror the deterministic contract).

### H4 — Scenario compare holds the process-wide admission leases across DB reads
`backend/app/api/simulation.py:608-624`

`run_compare` executes inside `_with_run_limits`, but fetches every scenario
(`await _get_scenario(db, …)` per id), builds Pydantic snapshots, and sums run
cost **before** `await db.rollback()` (line 615) — holding the user/farm/global
leases (`_global_run_slots = BoundedSemaphore(2)`, `_run_limits.py:110`) across
request-scoped, pool-bound DB work. Every sibling run path snapshots first, then
enters the lease. Under pool saturation the compare stalls holding 2 of 2 global
slots and starves all other simulation traffic process-wide. **Fix:** move the
fetch/validate/rollback block outside `_with_run_limits` (snapshot first, as the
comment at :613-614 already intends).

## 3. Medium findings (20) — grouped

**Backend correctness edges (02, 03):**
- Kidding is impossible for a pregnant doe history-overridden into BREEDING —
  no `(BREEDING, RECOVERY)` kidding edge in `LEGAL_BUCKET_TRANSITIONS`
  (models/lifecycle.py:34-63) while the override guard
  (api/animals.py:1000-1024) explicitly permits parking her there; the kidding
  workflow 409s forever. *(Verified: the graph only anticipates the loss path.)*
- Screening drops gate observations for regions whose specialist call failed
  (services/screening/pipeline.py:929-995) — permanently absent from the vet
  queue because FLAGGED is terminal.
- Insurance renewal duty spawned already-overdue for horizons ≤ 30 days
  (services/finance.py:193 + guards :324/:400) — contradicted by the helper's
  own docstring. *(Verified.)*
- Digest fan-out not resumable per recipient after a mid-fan-out crash
  (notifications/service.py:474-483) — readiness gate settles the whole farm on
  any recipient's settled row.

**Client data layer (07):**
- 5xx asymmetry: immediate attempts during 502/503/504 are non-queueable while
  the drain keeps 5xx records (offline-queue.ts:196-207).
- `AbortSignal.any`/`timeout` hard dependency with no fallback — Safari <17.4
  misclassified as queueable → false "Saved — will send when online"
  (api-client.ts:733-734).
- Queue records trusted on read; persisted headers replayed verbatim
  (offline-queue.ts:60-82,283-296).

**Frontend surfaced defects (05, 06):**
- Health administration-route enum codes (`SC/IM/IV/…`) render raw on screen —
  violates the codebase's own enum-labels contract (health/page.tsx:125-128,1732-1737).
- Tasks Completed tab + purchase-batch detail render raw status codes
  (`SKIPPED`, `PENDING`) — no `taskStatus` label family exists
  (tasks/page.tsx:288,457; purchases/page.tsx:369).
- Worker board `crypto.randomUUID()` called before the try block — silently
  no-ops Complete/Skip on non-secure origins, bypassing the
  `getRandomValues` fallback that exists in lib (worker/page.tsx:169). *(Verified.)*
- StatusBadge English-only at finance/team call sites where `enumLabel` exists
  ten lines away; hardcoded " — cull candidate (owner only)" in a shared picker.
- Consolidated i18n residue: hardcoded English fragments ("batch #N", "(due …)",
  "N mo", "kg", aria "access status", English-only `farmTypeLabel`, literal "←").

**Simulation & data layer (08, 04):**
- `c8f1d3a5e709` same-id variant ambiguity — an earlier variant of this
  migration silently zeroed non-finite money; applied databases carry no marker
  of which variant ran (confined to pre-release DBs per its own docstring).
- `updated_at` bump is ORM-only (`onupdate`/`server_onupdate` emit no DDL/trigger)
  — direct SQL updates skip it (models/animals.py:295-300, core.py:141-146).
- Daily-ops vs monthly engine divergences (adult mortality on weaned growers,
  boundary-stock pricing), 30.44-day-month vs calendar-month mixing in
  calibration (±2% class-rate bias), uncapped ops-sim output growth (~11× priced,
  not capped), ~100 MB MC trajectory memory at schema max — see track 08 for
  the full five.

**Infrastructure (09):**
- Production secrets delivered via environment variables (visible in
  `docker inspect`) while JWT PEMs and DB CA already use file mounts — close the
  gap by moving the remaining secrets to files
  (docker-compose.production.yml:94-165,199-235).

## 4. What is systematically strong (cross-track convergence)

Independent auditors — who could not see each other — converged on the same
strengths, which is the strongest possible signal:

1. **Tenant isolation is airtight.** Track 01 walked ~120 endpoint query paths:
   every one carries farm/ownership predicates with PK-band guards and shared
   404s; no IDOR, no enumeration, no raw SQL with user input, no
   `dangerouslySetInnerHTML` anywhere; `X-Farm-Id` parsed defensively; SW is
   network-only for `/api` so no cross-tenant cache risk.
2. **Concurrency discipline is engineered, not accidental.** Canonical lock
   orders (animals → breeding → tasks; Farm-advisory → Animal → Task),
   `FOR UPDATE` with post-lock revalidation, `nowait` + SQLSTATE 55P03 retryable
   409s, `ON CONFLICT` arbitration for recurrence/idempotency claims, epoch/
   generation fencing and single-flight on the frontend. Track 02 explicitly
   failed to construct a failing interleaving.
3. **Exact numerics everywhere money lives.** `Decimal` + `ROUND_HALF_UP` via
   `str`, remainder-correct paise allocation, no float currency anywhere
   (tracks 03, 04); simulation math verified by execution to 5.7e-14 mass
   balance (track 08).
4. **Fail-closed posture across layers.** Production boot refuses ~15 insecure
   config classes; migrations classify-and-refuse instead of rewriting; unknown
   env vars rejected in all three settings projections; compose `:?`
   interpolation + preflight guards; no CI gate found that can silently pass.
5. **The test estate is real.** Track 10 recomputed every headline number:
   backend 98.79% mutation score, frontend 80.8% (my independent recompute:
   80.82%), 4,908 collected tests, 92.54% coverage — and found one mock in the
   entire backend suite, zero skips/onlys/snapshots across 313 frontend test
   files, and an anti-blindness `>=80 routes` assert inside the tenancy matrix.
   The one stale artifact is the umbrella `MUTATION_TESTING_REPORT.md` (one
   commit behind `CAMPAIGN.md`).

## 5. Where defects cluster

1. **Sanctioned-exception paths** (history overrides, fallback chains) — the
   state machine's escape hatches haven't been re-checked against every
   downstream consumer (H-adjacent Medium in 02; the BREEDING/kidding gap).
2. **The notifications subsystem relative to its siblings** — the digest got
   the 2026-09-29 hardening; the alert fan-out and readiness gate did not
   (H1 + two Mediums all live here).
3. **Component-local policies colliding at session boundaries** — the queue's
   durability policy vs `clearSession`'s shared-tablet hygiene (H2); failure-
   classification taxonomies duplicated across api-client/queue/drain drifting
   at the edges (5xx, TypeError-name heuristics).
4. **"None/unmeasurable" not propagating into derived statistics** (H3) and
   two engines, one product (daily-ops vs monthly divergences).
5. **Presentation-layer enum/i18n residue** — raw codes and English fragments
   escaping the (otherwise exemplary) label machinery at ~10 call sites; the
   intended `scan-english-literals` lint pass would catch this class.

## 6. Recommended fix order

1. **H1** (one join filter — also closes a paid-SMS privacy disclosure) — smallest diff, highest immediate value.
2. **H2** (session-boundary queue wipe — offline write loss, the product's core promise).
3. **H3** (`prob_dscr_below_one: float | None` — misleading lending statistic).
4. **H4** (move snapshot before lease acquisition).
5. Mediums: notifications resumability + budget undercount; BREEDING/kidding
   edge; screening partial-failure gate; insurance lead guard; Safari
   `AbortSignal.any` fallback; enum-label families (`taskStatus`,
   administration routes) + i18n residue sweep; secrets-to-files in production
   compose; untrack `frontend/mutation` bulk from the frontend build context.
6. Lows at maintainer discretion — each is documented with evidence in its
   track report.

## 7. Bottom line

This is an unusually well-engineered codebase and — equally rare — an honest
one: every quantitative quality claim the repo makes about itself reproduced
exactly under independent recomputation. No Critical findings exist. The four
Highs are narrow, targeted defects in otherwise best-in-class subsystems, all
confirmed against source with bounded blast radius and straightforward fixes.

## 8. Remediation status (2026-10-02)

Every actionable finding from all ten tracks was fixed, each with regression
tests that fail against the pre-fix code (verified by the fixing engineers via
stash round-trips), and the full verification battery was re-run green.

### The four Highs — fixed and lead-verified in source

- **H1** `notify_alert_class` now joins `FarmMembership`/`User` and filters
  `is_active`/`deleted_at` exactly like the digest path
  (services/notifications/service.py:548-593). Regression tests cover all five
  alert classes and tombstoned accounts.
- **H2** `clearSession` now takes `{wipeQueuedOfflineWrites}`: explicit sign-out
  and end-of-shift still wipe (shared-tablet hygiene), but session death
  (rejected refresh) never destroys queued writes, and the drain re-persists
  kept records if storage was cleared mid-drain under the same actor scope
  (auth-context.tsx, offline-queue.ts). Foreign-actor records are never
  resurrected or replayed.
- **H3** `prob_dscr_below_one: float | None` — `None` when no run had a
  measurable DSCR, mirroring the deterministic contract; narrative and figures
  updated; contract regenerated (openapi.json + orval client) with the UI
  rendering null like the deterministic case (montecarlo.py:428-503,
  results.py:265, api/simulation compare parity).
- **H4** Scenario compare snapshots fetch/validate/rollback BEFORE entering
  `_with_run_limits`; the two process-wide slots are now held only around
  engine work (api/simulation.py:608-640, with a parked-lease test).

### Per-track remediation summary

- **01 (security):** per-farm probe budget on the worker-roster endpoint
  (kills sequential-farm crawling under IP rotation, per-IP limit unchanged);
  register duplicate-email oracle downgraded to a soft per-email ceiling that
  can never lock out a fresh-address registration; `sms_safe_text()` URL
  neutralizer applied at every free-text interpolation site; 01-3 accepted
  tradeoff documented (single-worker topology).
- **02 (livestock):** `(BREEDING, RECOVERY)` kidding edge added (workflow-only
  forging still impossible); per-kind specialist-failure gate observations
  recorded as findings; fallback-chain spend billed per failed paid attempt;
  SQL latest-weight twin aligned with the model for no-DOB animals; gestation
  and chronology violations normalized 409→422 (adversarial pins updated).
- **03 (ops):** H1 above; renewal duty never born overdue; digest readiness
  gate per-recipient (resumable after mid-fan-out crash); digest headline
  counts without the listing LIMIT; Bakrid clamp explicit; cadence dedupe on
  `title_key`.
- **04 (data layer):** migration `e7b9d1f3a5c2` adds the screening_images
  `updated_at` trigger (raw-SQL writers) + `DEFAULT false` server defaults for
  the notification opt-ins, with a downgrade round-trip test;
  `restore_floor.sh` allowlist extended (deployment-artifact tests green);
  StrictBool on kidding/notification inputs (422 on `"true"`/`1`); historical
  migration caveat documented; orval header-gap tripwire tests added.
- **05 (frontend livestock):** worker duty keys via the getRandomValues
  fallback helper; `adminRoute` label family (en+te) replacing raw SC/IM/IV
  codes; every hardcoded-English site catalogued into en+te keys; busy-id Set
  on the worker board; explicit locale tag sort; midnight-safe kidding window;
  dead guard removed.
- **06 (frontend ops):** `taskStatus` label family across tasks/purchases;
  StatusBadge localized at finance/team; cull-suffix catalog key; dialog
  dismissal parity with finance pages; `formatMoneyDecimal` in the owner
  console; shared overdue/due-soon predicates; pager `replace` + echoed
  limits; planner surfaces minimum-cash/working-capital.
- **07 (offline layer):** H2 above; 5xx classification unified between
  immediate and drain paths; `AbortSignal.any`/`timeout` fallback for older
  Safari; queue records re-validated against the replay allowlist on drain and
  header-persistence sanitized to an allowlist; row-scoped optimistic rollback;
  farm-scoped success fencing; queue depth scoped; `formatPersistedKg` locale
  routing; farm-timezone datetime inputs.
- **08 (simulation):** H3+H4 above; daily-ops kid/grower mortality classes;
  boundary fallback ages passed explicitly; calibration month convention
  unified (30.44-day); ops-sim hard output cap (`max_result_head_days`, 422);
  empty-herd cultivation skip; planner attrition fix (parity-verified);
  trajectory memory ~9× smaller (array-per-month); pass-count constants
  exported and priced from one source. DEFERRED for business owner: re-baselining
  the deeply loss-making default preset (framing corrected in-code).
- **09 (infra):** production secrets moved to file delivery (`*_FILE` wins,
  plain env still works; guard enforces exactly-one-of for required secrets);
  `mutation/` excluded from the frontend build context (verified with a real
  docker build); digest-lockstep test extended to the production compose file;
  README "signed" claim corrected; ci.yml reads `.nvmrc`.
- **10 (test integrity):** umbrella mutation report resynced to the committed
  artifacts; CI now fails on flaky e2e retries (JSON reporter + jq gate,
  local retries still 0); the 10 loose status pins tightened to settled codes;
  m09869 reclassified with a killing test (verified against the mutant).

### Verification battery (all green)

| Gate | Result |
|------|--------|
| Backend full suite (`uv run pytest tests`) | **4,973 passed, 4 skipped** (+65 new regression tests) |
| `ruff format --check .` / `ruff check .` | clean (376/376 formatted) |
| `mypy --strict app scripts` | clean (139 files) |
| tests mypy ratchet | holds at 1778 (new test code fully typed) |
| Contract drift | openapi.json + orval client regenerated; diff is exactly the intentional H3 change |
| `pnpm typecheck` / `pnpm lint` | clean (two pre-existing lint failures also fixed) |
| vitest default / libcore / components | 5,077 / 539 / 494 passed |
| Playwright e2e | not runnable in this environment (needs the live stack); config verified, flaky gate dry-run tested |

Known follow-ups: e2e execution against the docker stack; mutation-campaign
re-run to re-score the touched modules (campaigns are offline artifacts);
default-preset economics awaiting the business owner.

## 9. Independent verification of the remediation (2026-10-02)

Every fix was re-verified independently of the engineers who wrote it, using a
mechanical protocol: revert the fix's source files to the pre-fix state
(`main`), run the claimed regression test — it must FAIL — restore, re-run — it
must PASS. All fixes were first committed to a safety branch
(`audit-remediation-2026-10-02`) so round-trips could never destroy work.

### Verdict matrix

| Stage | Scope | Verified (round-trip) | Verified (static¹) | Not load-bearing² | Failed |
|-------|-------|----------------------:-------------------:|------------------:|-------:|
| Lead | 4 Highs (H1–H4) | 4 | — | — | 0 |
| Verifier 1 | tracks 01, 02, 03 | 13 | 1 | 1 (03-5) | 0 |
| Verifier 2 | tracks 05, 06, 07 | 22 | 1 | 2 (07-L4, 08-tint) | 0 |
| Verifier 3 | tracks 04, 08 | 10 | 4 | 3 (08-M4, L9, L14) | 0 |
| Verifier 4 | tracks 09, 10 + consolidation | 9 | 1 | — | 0 |
| **Total** | **all 70 remediation items** | **58** | **7** | **6** | **0** |

¹ Comment/doc-only fixes and coverage-gap fixes where the test IS the fix —
verified by diff review, grep of the audit markers, and running the new tests.
² Fix present and judged correct, but its regression test also passes on the
pre-fix code in this environment: 03-5 and 08-M4/L9/L14 are deliberate
invariant pins (numeric-neutral today, documented in their docstrings — the
Bakrid clamp additionally cannot diverge on PostgreSQL 16, now noted in the
test); 07-L4 and the 08-H1 null-tint test were genuinely weak and were
**strengthened** in the verification round itself (delegation recorded via a
formatNumber mock; tone asserted on a new `data-tone` semantic attribute added
to StatCard per the repo's own AGENTS.md state-styling rule). Both strengthened
tests now fail against the pre-fix source and pass against the fix.

Failure modes observed in round-trips (all valid proof): assertion failures and
collection/import errors where the fix introduced a symbol the tests import.

### Post-verification gates (all green)

- Backend: full suite 4,973 passed / 4 skipped; ruff format + lint; mypy
  --strict app+scripts; tests ratchet at 1778.
- Frontend: typecheck; lint (max-warnings 0); vitest default 5,077 /
  libcore 539 / components 494 — all passed after the StatCard attribute
  addition and test strengthening.
- Contract: openapi.json + orval client regenerate with zero drift beyond the
  intentional H3 change.
- m09869 kill re-proven under the campaign's own MUTANT_ID protocol; mutation
  report numbers independently recomputed twice (both match).

### Residual notes

- The three by-design invariant pins (03-5, 08-M4, 08-L14, 08-L9) protect
  against future divergence rather than today's values; their docstrings now
  say so explicitly.
- Playwright e2e remain unexecuted in this environment (need the live stack);
  the flaky-failure gate added to CI was dry-run verified on synthetic reports.
- Deferred business decision unchanged: re-baselining the default simulation
  preset.
