# Track 5 — Offline/PWA reliability

Audit date: 3 October 2026 (America/Phoenix)  
Audited commit: `1ca78768ed227182b1b84663bcc97c5ea9bee41b`

## Verdict

The worker tablet has a substantially stronger offline write path than a typical local-storage queue: a duty is committed to IndexedDB before its first request, retains one idempotency key, is scoped to actor and farm, and is replayed through a cross-tab lease and a live session/farm fence. The backend commits the idempotency receipt and domain mutation in one PostgreSQL transaction. I found no critical or high-severity defect.

Five reliability gaps remain. Three affect realistic outage or review workflows; two are hardening issues that become material after storage damage or sustained deployment churn.

## Findings

### 05-1 — A fast HTTP error wins over a healthy cached worker shell

- Severity: **Medium**
- Confidence: **High**
- Evidence status: **Reproduced** with an executed service-worker harness
- Evidence: `frontend/public/sw.js:112-127`

The navigation race treats any resolved `Response` as success. Cache publication correctly requires `response.ok` at `frontend/public/sw.js:120-121`, but the response path at `frontend/public/sw.js:123-126` uses `response ?? cached`, not `response?.ok ? response : cached`.

Concrete interleaving:

1. `/worker` is already cached and usable.
2. The tablet can reach the origin, but a rolling deploy, overloaded Next server, or reverse proxy quickly returns 500/502/503.
3. That non-OK response resolves before the four-second timer and is returned to the navigation.
4. The cached shell is never consulted, so the worker sees the error response despite having a valid offline UI locally.

I executed `sw.js` with a cached `200 "cached-shell"` and a network `503 "upstream-503"`; the selected response was `{"status":503,"body":"upstream-503"}`. This is distinct from a rejected or wedged fetch, both of which the implementation and tests handle.

### 05-2 — “Lie-fi” holds the cached offline screen behind about 31.5 seconds of authentication retries

- Severity: **Medium**
- Confidence: **High**
- Evidence status: **Source-proved; timing not exercised against a real captive portal**
- Evidence: `frontend/public/sw.js:16-19`, `frontend/public/sw.js:123-126`, `frontend/src/lib/api-client.ts:126-135`, `frontend/src/lib/api-client.ts:269-279`, `frontend/src/lib/auth-context.tsx:590-619`, `frontend/src/app/worker/layout.tsx:175-190`

The service worker deliberately exposes a cached shell after four seconds, but the app fast-paths offline bootstrap only when `navigator.onLine === false`. When that flag remains true on a captive portal or black-holed Wi-Fi connection, bootstrap performs three sequential refresh attempts. Each refresh has a 10-second timeout, with two 750 ms waits between attempts. Meanwhile `WorkerShell` returns only its loading screen before it reaches the special-case rendering for `/worker/offline`.

Concrete interleaving: a worker reloads `/worker/offline` after connectivity loss; the network link still reports “online” but drops requests. The service worker supplies the cached HTML at about four seconds, yet the saved 12-hour shift cannot render until roughly `3 × 10 s + 2 × 0.75 s = 31.5 s` (plus scheduling overhead). The data is not lost, but the principal offline recovery screen appears hung during a common poor-connectivity mode.

### 05-3 — Review receipts have no resolution path, block that duty forever, and consume a device-wide active cap

- Severity: **Medium**
- Confidence: **High**
- Evidence status: **Reproduced by the repository unit suite; cap amplification is source-proved**
- Evidence: `frontend/src/lib/worker-outbox.ts:173-184`, `frontend/src/lib/worker-outbox.ts:319-340`, `frontend/src/lib/worker-outbox.ts:227-255`, `frontend/src/app/worker/layout.tsx:311-325`, `frontend/src/lib/worker-outbox.test.ts:75-85`

A definitive 4xx or an operation older than 72 hours becomes `review`. A later save with the same actor, farm, path, and body finds that record and throws `OutboxReviewRequiredError`; only `sent` receipts can be cleared. The UI can display/export a review receipt but offers no acknowledge, resolve, or retry action.

Concrete interleaving:

1. A saved completion receives a temporary-but-definitive business conflict—for example a 403 before access is restored, or a 409 standing idempotency-capacity response.
2. The outbox retains it as `review`.
3. Conditions are corrected and the task is again valid and pending.
4. The same worker presses Complete. `persistWorkerOperation` returns the old review match and refuses the new action before any request is made.

The focused unit suite explicitly reproduces this terminal behavior after a review receipt. It is amplified across the whole tablet: `MAX_PENDING` and `MAX_BYTES` count every non-`sent` record from every actor and farm, not just the current scope. Revoked/departed workers and old farms can therefore consume the 100-record/2 MiB active allowance, while the signed-in worker cannot see or resolve those foreign receipts. At the bound, every worker on the device is refused a new offline action until site data is manually repaired or cleared.

### 05-4 — The live PWA cache accumulates orphaned hashed assets across deployments

- Severity: **Low**
- Confidence: **High**
- Evidence status: **Source-proved; long-horizon quota exhaustion not reproduced**
- Evidence: `frontend/public/sw.js:14-15`, `frontend/public/sw.js:33-40`, `frontend/public/sw.js:76-81`, `frontend/public/sw.js:97-109`

All `/_next/static/` misses are appended to the constant `herdly-worker-v3` cache. Activation deletes caches with other names but never prunes entries inside the current cache. New Next deployments produce new content hashes, so old chunks become unreachable but remain stored; route visits add still more entries.

Concrete interleaving: deploy A caches A's chunks; deploys B through N change hashes while `sw.js` retains the same cache name; network-first shell refreshes and cache-first asset loads add every build's chunks, and activation preserves the one shared cache. On a storage-constrained farm tablet this eventually turns fresh-shell caching into quota failures (the navigation cache error is suppressed), leaving an older offline shell, or invites browser origin eviction. No size/count bound or stale-entry reconciliation exists.

### 05-5 — One malformed legacy queue record permanently wedges the new IndexedDB outbox

- Severity: **Low**
- Confidence: **High**
- Evidence status: **Source-proved; malformed-site-data scenario not runtime-reproduced**
- Evidence: `frontend/src/lib/worker-outbox.ts:79-110`, `frontend/src/lib/worker-outbox.ts:115-151`, `frontend/src/lib/worker-outbox.ts:153-168`, `frontend/src/app/worker/layout.tsx:310-325`

Legacy migration parses the entire `goatfarm:offlineQueue:v1` array atomically and throws `OutboxStorageError` if any member is malformed. It intentionally leaves the source intact, but every subsequent read and every new persist calls migration again, encounters the same record, and fails again. IndexedDB may itself be healthy and already contain pending work, yet the worker sees only the generic storage/full warning and cannot inspect, export, replay, or add duties through the UI.

Precondition: an upgraded tablet retains a partially incompatible/tampered legacy record or malformed JSON. Impact is bounded to that browser profile, but recovery currently requires developer-level site-storage repair and risks deleting the very evidence the fail-closed behavior tries to preserve.

## Controls that held

- `frontend/src/lib/worker-outbox.ts:39-73` acknowledges writes only after a strict IndexedDB transaction commits.
- `frontend/src/lib/worker-outbox.ts:258-347` combines actor/farm filtering, a cross-tab lease, FIFO sequencing, original-key replay, session/farm checks, and retained 4xx review receipts.
- `frontend/src/lib/api-client.ts:407-445` adds session and farm epochs, so leaving and returning to the same identity/scope does not revive a stale async continuation.
- `backend/app/services/idempotency.py:300-427` arbitrates concurrent keys in PostgreSQL and commits the domain change and replayable response together; `backend/app/models/idempotency.py:26-65` scopes uniqueness by farm, actor, operation, and key digest.
- `frontend/public/sw.js:85-95` keeps `/api/` network-only; operational reads are not satisfied from a stale cache.

## Verification performed

- `cd frontend && pnpm vitest run src/lib/worker-outbox.test.ts src/test/adversarial/adv-sw-update.test.ts --reporter=dot` — **2 files, 35 tests passed**.
- Executed `frontend/public/sw.js` in a stubbed worker environment with a cached healthy shell and a fast network 503 — reproduced finding 05-1.
- Static tracing covered worker shell/login/offline UI, IndexedDB and legacy migration, offline shift snapshot, authentication bootstrap and cross-tab scope fences, API retry/idempotency transport, service-worker install/activate/fetch/update logic, backend task idempotency, and the focused unit/E2E tests.

## Limits

No application code was changed. I did not run a real installed PWA through OS process death, captive-portal hardware, browser quota eviction, or a multi-device production deployment. The documented 12-hour offline snapshot intentionally works only in the same browser tab/session; cold-launch recovery after that tab/session is lost is out of scope by current product design (`README.md:864-870`), not counted as a defect here. Background Sync and push are likewise explicitly out of scope.

## Counts

| Severity | Count |
|---|---:|
| Critical | 0 |
| High | 0 |
| Medium | 3 |
| Low | 2 |
| Info-only findings | 0 |
| **Total defects** | **5** |
