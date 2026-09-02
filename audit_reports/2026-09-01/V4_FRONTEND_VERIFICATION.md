# V4 — Independent Frontend Remediation Verification

**Date:** 2026-09-01
**Verifier:** V4 (independent; did not author any remediation)
**Repo:** `/Users/pavankumarreddyreddem/Desktop/goat_saas/frontend`
**Claims verified:** `00B_REMEDIATION_LOG.md` (F-1 bullet + P3 frontend paragraph) against `08_FRONTEND_ADVERSARIAL_AUDIT.md` (M-1, L-2..L-6) and `03_CONTRACT_CONSISTENCY_AUDIT.md` (L-2, L-3, L-5).

**Method.** Read-only on `src`. Behavioral probes written as temporary test files
(`*.v4-verify.test.tsx`) mirroring the audited code's own test patterns
(`animals/page.test.tsx` navigation mock + MSW, `layout.test.tsx` shell setup,
`tasks/page.test.tsx` fixtures), executed with the repo's vitest config, then
**deleted** — tree left exactly as found (`git status` shows only the
pre-existing remediation modifications; zero verifier remnants, `find` confirms).
No dev servers; no persistent repo edits.

**Verdict summary: 10 PASS (one with a documented caveat), 0 FAILED, 0 PARTIAL.**

---

## 1. F-1 / MEDIUM-1 — Animals list hostile-URL filter validation → PASS

**Claim:** list validates `?bucket/sex/status` against the generated enums
(fallback ALL, never a guaranteed-422 request); error branch offers Clear
filters; strict page grammar.

**Code read (`src/app/(app)/animals/page.tsx`):**
- `bucketFromSearchParams`/`sexFromSearchParams`/`statusFromSearchParams`
  (lines 302–318): `Object.values(<generated enum>).includes(raw)` else `ALL` —
  applied both at seed time (lines 845–847) and in the `paramsKey` re-sync
  effect (lines 964–966). Params forwarded only when `!== ALL` (lines 1029–1033).
- `pageFromSearchParams` (lines 290–297): strict `/^(0|[1-9]\d*)$/` grammar —
  the tasks-board pattern; `0x10`/`1e2`/whitespace fall back to 1.
- Error branch (lines 1288–1300): `Clear filters` button rendered when
  `filtersActive`, next to `Retry animals`.

**Probe (4 tests, mirroring the auditor's `/tmp/a8probe` technique — MSW handler
mirrors FastAPI's enum 422 for any garbage value):**
- `?bucket=EVIL_BUCKET&sex=BANANA&status=ZOMBIE&page=0x10` → **zero** requests
  carrying any garbage value reached the handler (`garbageHits` empty; every
  recorded URL clean); the page **rendered** the herd ("G-001", "1 animal(s)")
  instead of erroring; the query settled after 1–4 fetches (no retry loop);
  first request carried **`offset=0`** (`page 0x10` normalized to page 1; limit 50).
- Valid deep link `?bucket=QUARANTINE&sex=F&status=SOLD` → all three forwarded
  verbatim; `include_all_statuses` correctly absent (validation does not
  over-discard legitimate links).
- Error branch with an active filter (500 response): both `Retry animals` and
  **`Clear filters`** rendered inside the `role="alert"` region; clicking Clear
  reset the Select roots to "All buckets".
- Error branch with no filters: `Clear filters` correctly absent.

All 4 tests passed. The auditor's probe demonstrated the exact opposite
(raw detail rendered, identical re-request on Retry, no Clear filters) — the
dead end is gone.

## 2. Rotation banner → PASS

**Code read:** `src/app/(app)/app-layout-client.tsx:399-410` — `user.must_change_password`
gates an amber `role="alert"` banner ("This password was set by the farm owner —
change it (Account → Change password) before recording any farm work. Farm
actions are blocked until you do."). `user` is the auth-context `SessionUser`
(`UserOut`).

**Probe (2 tests, `layout.test.tsx` patterns — matchMedia shim, nav mock,
`renderWithProviders` with the real AuthProvider):** overriding
`POST /api/auth/refresh` to return `{...TEST_USER, must_change_password: true}`
rendered the banner (asserted text + `role="alert"` ancestor + the
"Account → Change password" instruction); the default flag-less user rendered
**no** banner. Both passed.

## 3. 422 operator copy → PASS

**Code read:** `src/lib/api-client.ts:445-475` — `extractDetail` maps FastAPI
`detail[]` arrays to `The server rejected these values (<loc>: <msg>; …). Check
the entered data and try again.` for status 422 (empty-specifics fallback
sentence; non-422 arrays keep the bare specifics join).

**Existing tests (run, all green):** `api-client.test.ts:90-106` feeds a real
pydantic-shaped 422 array (`[{loc:["body","tag_number"],msg:"field required"},…]`)
and asserts the exact friendly sentence with specifics; `api-client.extended.test.ts`
covers string/odd-entry arrays and the no-specifics fallback;
`custom-instance.test.ts:116` pins the same mapping through the Orval mutator.
No extension needed — coverage already asserts the audited behavior.

## 4. LOSS-cause labels → PASS

**Code read:** `src/lib/enum-labels.ts:68-76` — `lossCause` kind covers all 7
backend causes incl. `ANIMAL_STATUS_CHANGE: "Herd exit (administrative close)"`;
`breeding/page.tsx:1050` routes rendering through `enumLabel("lossCause", …)`
(no raw `.replace(/_/g," ")` path left).

**Existing test (run):** `breeding/page.outcome-copy.test.tsx` — 26/26 passed,
including a record with `loss_cause: "ANIMAL_STATUS_CHANGE"` asserting the
rendered "…· Herd exit (administrative close)" label (line 256).
`enum-labels.test.ts` 5/5.

## 5. STATUS_TONES DEAD/SKIPPED → PASS

**Code read:** `src/components/status-badge.tsx` — `SKIPPED: "info"` (line 32),
`DEAD: "destructive"` (line 49).

**Probe (4 tests):** `statusTone("DEAD")==="destructive"`,
`statusTone("SKIPPED")==="info"`, normalization ("dead"/" Skipped ") holds,
unknown value ("ZOMBIE") stays `null` (neutral, no wrong tone). All passed.
(No prior test referenced `statusTone`; this unit check is new evidence.)

## 6. Sparkline filtered-length x-scale → PASS

**Code read:** `src/components/charts.tsx:28,53` — `x = (i / (values.length - 1)) * width`
with `values = data.filter(Number.isFinite)`.

**Probe (3 tests, one NaN):** `data={[1, NaN, 3]}` → polyline has exactly 2
points with the last finite point at **x = 96 (full width)** — the pre-fix
unfiltered-length scale would have stopped it at 48; end dot `cx = 96`;
area polygon closes at 96 (also with leading/trailing NaN). All passed.

## 7. Caps parity test — PASS (with caveat)

**Run:** `src/lib/backend-caps.test.ts` — 6/6 passed.

**Teeth proof (mutant, in-tree copy deleted after the run — repo untouched):**
a copy of `backend-caps.ts` with exactly one constant mutated
(`MAX_ANIMAL_TAG_LENGTH` 50 → 51), re-asserted with the test's own
`schemaBound` logic against `shared/openapi.json`, **failed** as required
(`expected 50 to be 51`) — the parity test genuinely reads the generated
contract, so a drifting constant fails CI. Same holds by inspection for title
(200), recur (3650), batch count (1000) and age (240): all compared directly
to `PurchaseBatchIn`/`TaskCreateIn` bounds (tag bound correctly dug out of the
optional `anyOf` variant).

**Caveat (found by the informational probe, worth a follow-up):** two of the six
assertions degrade to tautologies — the withdrawal assertion looks up
`HealthEventIn.withdrawal_days` and the notes assertion `TaskCreateIn.notes`,
but **neither property exists in the spec** (HealthEventIn uses `withdrawal_until`;
the 4000 notes bound lives at `HealthEventIn.notes.maxLength`). Their
`bound === null ? caps.X : bound` fallback then compares the constant with
itself, so drift in `MAX_WITHDRAWAL_DAYS`/`MAX_FREE_TEXT_LENGTH` would NOT be
caught. The other four constants are genuinely fenced.

## 8. Generated client freshness → PASS

- `src/api/generated/models/userOut.ts:12` — `must_change_password?: boolean`
  (orval 8.23.0 header); `shared/openapi.json` `UserOut` carries the property
  (`boolean`, default false).
- `src/api/generated/models/taskOutStatus.ts` — const-union
  (`PENDING|DONE|SKIPPED|VERIFIED`); `taskOut.ts` types `status: TaskOutStatus`;
  spec side is `"type": "string", "enum": [...]`.
- `shared/openapi.json` regenerated **after** the backend changes (mtime
  2026-09-01 15:54; content includes the rotation flag and the ~50 enum-typed
  Out fields from the contract remediation).

## 9. Full gates → PASS

- `node_modules/.bin/tsc --noEmit` — **exit 0, clean** (run on the clean tree
  after verifier files were removed).
- `node_modules/.bin/vitest run` — **155 files / 3,264 tests, all passed**
  (401 s) — exactly matches the remediation log's claim.

## 10. Quick regressions → PASS

- **`/animals/new` shim:** `animals/new/page.tsx` merges `new=1` into the
  caller's query instead of discarding `search`. Probe (2 tests): with
  `?returnTo=/tasks?tab=today` the single `router.replace` target parsed back
  to `new=1` **and** `returnTo=/tasks?tab=today`; bare path still redirects to
  `/animals?new=1`. (The shipped `new/page.test.tsx` only covered the bare
  path; the returnTo case had no prior coverage — now verified.)
- **Tasks future-locked dutyless row:** probe (1 test) rendered a
  `BUCKET_MOVE` auto duty due tomorrow with `action_url: null` → row shows
  "**Not due yet — actions open on the due date.**" with no Complete/Skip
  buttons (previously a completely blank action cell). The with-form variant
  ("Not due yet — the linked form opens on the due date.") is covered by the
  existing `tasks/page.test.tsx` (10/10 passed).

---

## Evidence ledger (executed 2026-09-01, all temp files deleted afterward)

| Run | Result |
|---|---|
| V4 probes: animals F-1 (4), banner (2), sparkline (3), shim (2), tasks dutyless (1), statusTone (4) | 16/16 passed |
| V4 teeth mutant (`MAX_ANIMAL_TAG_LENGTH` 50→51) | failed as designed (`expected 50 to be 51`) — teeth proven |
| `backend-caps.test.ts` | 6/6 |
| `api-client.test.ts` + `.extended` + `custom-instance.test.ts` | 89/89 |
| `enum-labels.test.ts` | 5/5 |
| `breeding/page.outcome-copy.test.tsx` | 26/26 |
| `tasks/page.test.tsx` | 10/10 |
| `charts.test.tsx` | 18/18 |
| `tsc --noEmit` | clean (exit 0) |
| full `vitest run` | **155 files / 3,264 tests passed** |

**Bottom line:** every frontend remediation claim in `00B_REMEDIATION_LOG.md`
re-verified independently and held. One non-blocking gap found beyond the
claims: the caps parity test's withdrawal and notes assertions are vacuous
because they probe spec properties that do not exist (`HealthEventIn.withdrawal_days`,
`TaskCreateIn.notes`) — recommend repointing them at `HealthEventIn.notes`
(maxLength 4000) and either the real withdrawal bound or an explicit
`spec-shape` tripwire, so all six constants are genuinely fenced.
