# Independent audit: tracks 20–26

Audited commit: `7245368fa91333fb39387df789fa4ef9a58dbea3` on 2026-10-04. This review inspected current application code, operations scripts, Compose manifests, Dockerfiles, workflows and tests. It did not use previous audit conclusions. Application source was not changed. All external-provider and release-deletion probes used local fakes; the actual screening pipeline used only the disposable `goatfarm_test_a26_ops_worker` database, which the fixture removed after the run.

## Coverage and fresh verification

| Track | Substantive coverage | Result and limits |
|---|---|---|
| 20. Performance and capacity | Shared simulation admission, CPU-budget bookkeeping, cancellation/native-thread leases, SQL-side dashboard/feeding aggregation, capped owner/history queries, password-worker admission, database pool bounds, Compose CPU/memory limits. | Synthetic admission probe confirmed two occupied slots reject the third request with 429 in about 3 ms; cancelling the HTTP task does not release still-running native work; all keyed locks clear on completion. The HTTP-method metric label is explicitly normalized to `OTHER`, and route labels use templates. No additional defect confirmed. Production-scale latency, concurrent population, memory high-water marks and database query plans were not measured. |
| 21. Background work and external services | Screening worker configuration/heartbeat/restart policy, actual pipeline success and provider-error paths, retry budgets, provider HTTP adapters, notification send/outbox concurrency and ambiguous-delivery policy, retention maintenance. | O26-01. Full screening probe: **2 passed**, observing the failure with both successful and failed fake providers. No live S3, AI or SMS provider was contacted. |
| 22. Deployment and configuration | Development/production Compose isolation, least-privilege settings projections, secret-file/inline ambiguity guard, TLS requirements, container hardening, fresh current-revision container builds, configuration validation and runtime commands. | O26-01 crosses the worker projection boundary; O26-07 and O26-08 independently block the frontend Docker build. Current backend/edge images build. Shell syntax and workflow parsing pass. This was not an actual production deployment or verification of deployed secrets/TLS. |
| 23. Backup and recovery | Backup producer locking, atomic publication, GPG scope, checksum/inventory binding, freshness calculation, restore signature/empty-target/atomic transaction checks, restore floor, systemd schedules and current runbooks. | O26-04. Timestamp-tamper probe reproduced false freshness, then was strengthened with a genuine 72-hour-old GPG signature/encryption over an audit fixture using an owned temporary keyring. No database restore or off-site recovery drill was performed; production RPO/RTO remains unverified. |
| 24. Monitoring and operations | Private API metrics, process-local collectors, worker heartbeat, maintenance counters, cAdvisor/blackbox/node-exporter scrape configuration, alert rules, freshness textfile and off-host log requirements. | O26-02 and O26-04; O26-01 is also masked by the worker's normal `ok` cycle heartbeat because individual image errors do not throw from the cycle. Alert delivery and deployed exporters were not exercised. |
| 25. Dependencies, CI and releases | Frozen dependency manifests, hash-pinned bootstrap, patched dependency verifier, pinned workflow actions/container inputs, CI gates, release preflight, per-architecture scanning/signing and cleanup ownership. | O26-03, O26-07–09. Fresh backend Python dependency audit: **59 dependencies, no known vulnerabilities**. Frontend: **0 unmitigated, 1 locally mitigated braces advisory**, with 19 depth-guard and 4 ordinary-pattern checks passing. Fresh Trivy DB and digest-pinned scanner scanned four shipped bases/services and two current source builds: backend and PostgreSQL fail the repository's vulnerability policy; current edge passes. Current frontend image scan is blocked by O26-07/08. No actual release was performed. |
| 26. Test and evidence integrity | Existing integration-fixture boundaries, CI coverage/mutation evidence flow, raw attempt/summary agreement, failed/empty SARIF, scan/coverage artifact handling, immutable revision/OpenAPI gates. | O26-05 and O26-06. Exact CI Ruff check/format pass; exact separate mypy commands pass for application/scripts/CI tools (**154 files**), mutation harness (**12 files**), and tests (**153 files**). Full backend/frontend suites are covered by the other current audit tracks, not duplicated here. |

Evidence directory: [evidence/ops](evidence/ops). `actionlint.txt` is empty because actionlint passed. `shell-syntax.json` records five successful shell checks. The initial combined mypy command encountered duplicate module naming; the three exact, separate CI invocations then passed. That invocation issue is not an application finding. Dependency-audit cache warnings explicitly say the cache entries were ignored; the audit exited successfully and retained its full fresh result.

Container controls used the already available Docker/Colima daemon and pinned scanner/GPG tool images; no host scanner or GPG installation was needed. Docker's `/tmp` bind mount is VM-local in this environment, so the valid probes used a shared home directory and checked input-file presence before invoking the package manager. All audit-owned image tags/leaf images, the stopped failed-build container, scanner archives/database caches, temporary package fixtures, GPG keyring and temporary pip-audit environment were removed; [container-probe-cleanup.json](evidence/ops/container-probe-cleanup.json) records exact targets/results. Existing base/tool/app images and user stacks were untouched; no global prune was run, and ordinary Docker build-cache parent layers may remain. Independent peer review in [peer-review-ops.md](peer-review-ops.md) corroborates O26-01–04 and O26-07/08, including fresh countercontrols for both new build failures.

**Supplementary track 12 migration receipt:** The exact CI sequence `alembic upgrade head` → `alembic check` → `alembic downgrade base` → `alembic upgrade head` → `alembic check` passed all five commands on newly created `goatfarm_test_a26_migration_9b83417e`. Both database URL variables explicitly named only that disposable target, the ambient backend `.env` was verified absent, and the probe refused a pre-existing database. Each step independently checked `current_database()` and captured table/revision state; downgrade left only the empty Alembic marker table, and both metadata checks found no new upgrade operations. The owned database was dropped without FORCE and its absence verified. Full command/output/schema/cleanup receipts: [migration-roundtrip.log](evidence/ops/migration-roundtrip.log), [migration-roundtrip.json](evidence/ops/migration-roundtrip.json), [reproduction script](evidence/ops/migration-roundtrip.py). This adds empty-schema migration assurance; it does not establish populated historical-data upgrade/downgrade compatibility or production lock/TLS behavior. No additional finding resulted.

## O26-01 — High — Production screening consumes provider calls but terminally fails provider-processed images

**Tracks:** 21, 22, 24. **Confidence:** high, actual PostgreSQL pipeline reproduction.

**Prerequisite:** Enable screening in the supplied production worker topology. No attacker is required. The worker receives database/storage/provider settings, while API cookie, HMAC, TOTP and CORS settings are deliberately absent.

**Location:** `backend/app/metrics.py:89` (`enabled()` calls full API `get_settings()`), `backend/app/metrics.py:138`, `backend/app/services/screening/pipeline.py:875`, `backend/app/core/config.py:1714`, `docker-compose.production.yml:308`. Failure settlement is `backend/app/services/screening/pipeline.py:797`; terminal attempt handling is line 825. `backend/app/worker/__init__.py:203` publishes `ok` after this returned cycle.

**Reproduction:** From `backend`, run the retained `probe_worker_metrics.py` with `PYTHONPATH=.` and the venv interpreter. Valid production `ScreeningWorkerSettings` is accepted, but `_record_run` raises `ValidationError` when it constructs full API settings. The stronger retained `test_production_worker_cycle.py` drives the real `run_screening_cycle`, with the existing in-memory storage and provider doubles, on its own explicitly named test database. It tests a healthy provider and a provider outage over all five permitted attempts.

**Expected versus actual:** A valid worker configuration should produce a durable screening verdict/run or a durable provider-error run. Both actual cases instead produce image `ERROR`, zero `ScreeningRun` rows, and one durable call reservation per attempted provider call. After five attempts, the image is terminal with `unexpected screening failure; terminal after 5 attempts`. The provider-success case loses a valid healthy result. The provider-error case also fails while trying to record the failed attempt; fallback/error recording therefore does not avoid the defect. Independent peer review also reproduced the configuration failure with `GOATFARM_METRICS_ENABLED=false`: reading that flag itself constructs the invalid API settings. Images rejected before provider work are outside this failure claim.

**Impact:** Production screening is unusable under the documented credential separation; repeated real model calls would consume budget and money without a usable review result. The cycle catches the settings exception per image, so the worker can continue reporting healthy cycles instead of its consecutive-cycle-failure restart path firing.

**Recommendation:** Give metrics a worker-safe configuration source, or pass an explicit metrics enablement setting into the worker-owned collector. Do not make the worker receive unrelated API secrets to satisfy telemetry. Add an integration regression that runs the whole pipeline with only the production worker's delivered environment, for both successful and failed providers.

**Evidence:** [worker-metrics-result.json](evidence/ops/worker-metrics-result.json), [worker-cycle-pytest.txt](evidence/ops/worker-cycle-pytest.txt), [worker-cycle-provider-success.json](evidence/ops/worker-cycle-provider-success.json), [worker-cycle-provider-failure.json](evidence/ops/worker-cycle-provider-failure.json), [test_production_worker_cycle.py](evidence/ops/test_production_worker_cycle.py).

## O26-02 — Medium — Screening counters are collected in a process that has no scrape endpoint

**Tracks:** 24, 21. **Confidence:** high.

**Prerequisite:** Screening work runs in the separate worker process. This applies to development today and remains after O26-01 is corrected in production.

**Location:** `backend/app/metrics.py:27` creates a private process-local registry; lines 115–142 register/increment screening call and cost counters. `backend/app/api/operational_metrics.py:16` exports only the API process registry. `backend/app/worker/__init__.py:250` starts only the worker loop. `ops/prometheus/prometheus.scrape.example.yml:1` has an API scrape and no worker metrics scrape/exporter.

**Reproduction:** Run [probe_process_metrics.py](evidence/ops/probe_process_metrics.py). A worker-shaped Python process records a successful call and exposes a local sample with value 1. An independent API-shaped Python process imports and renders its registry; it contains no screening call sample.

**Expected versus actual:** The supplied screening call/error/spend telemetry should be available to the supplied scraper. Actual increments remain in worker memory; no worker HTTP exporter, shared multiprocess collector or durable API collector transfers them to `/metrics`.

**Impact:** Operators cannot use these advertised metrics to observe screening call failures or estimated spend through the shipped scrape path. The database call-budget ledger continues to enforce admission; this finding is about unavailable monitoring, not a budget bypass.

**Recommendation:** Expose a protected worker registry and configure its scrape, or export durable screening aggregates from the API. Use an integration check involving two actual processes rather than inspecting one registry in a test process.

**Evidence:** [process-metrics-result.json](evidence/ops/process-metrics-result.json).

## O26-03 — Medium — Re-running an existing release deletes package versions owned by the earlier successful run

**Tracks:** 25. **Confidence:** high for emitted deletion requests; deployed final-index availability was not tested.

**Prerequisite:** A successful version retains its `<TAG>-publish-amd64` and `<TAG>-publish-arm64` tags, an operator reruns that release, and the workflow token is allowed to delete package versions. Successful runs currently leave those temporary tags in place.

**Location:** `.github/workflows/release.yml:118` rejects the existing release; lines 508–538 then run cleanup on any failure. Only final `<TAG>` cleanup is gated by the assembly ownership marker; per-architecture publish tags at line 529 are always selected.

**Reproduction:** [probe_release_cleanup.py](evidence/ops/probe_release_cleanup.py) extracts the current preflight and cleanup shell scripts, substitutes a fake `gh` executable, and supplies a previously successful release plus its existing publish tags. Preflight exits 1 before any build or push. With no assembly ownership marker, cleanup nevertheless emits **six package-version DELETE requests**, two for each image repository.

**Expected versus actual:** Refusing to rerun an immutable version should leave that prior release's artifacts unchanged. Actual failure cleanup deletes pre-existing package versions it never created in this attempt. The inline claim that cleanup deletes only this run's temporary tags is therefore false.

**Impact:** Previously verified release artifacts/architecture references can be lost during a harmless rejected rerun. GHCR deletion operates on package versions, not just the matching tag, so additional tags attached to the selected version are also at risk. Whether the final assembled deployment index loses accessible child manifests depends on registry reference/garbage-collection behavior and is not claimed as reproduced here.

**Recommendation:** Name temporary references per run/attempt, record the exact version/digest objects successfully published by that attempt, and delete only that owned set. All cleanup must be a no-op when preflight fails.

**Evidence:** [release-cleanup-result.json](evidence/ops/release-cleanup-result.json). No real GitHub or registry mutation occurred.

## O26-04 — Medium — Unsigned recovery metadata can make an unchanged stale backup appear fresh

**Tracks:** 23, 24. **Confidence:** high.

**Prerequisite:** Ability to replace or manually rewrite the backup's `.recovery.json` sidecar in the local/off-site backup set. The normal `capture` and `bind` commands refuse overwriting existing output; this probe does not claim they perform that rewrite themselves. This is not an unauthenticated web exploit and does not require the GPG signing/decryption key. A trusted host with immutable authenticated sidecars would reduce this risk, but the shipped set does not authenticate them.

**Location:** `backend/scripts/backup.sh:431` signs/encrypts only the database dump; lines 458–474 bind and publish the recovery JSON afterward. `backend/scripts/recovery_inventory.py:287` validates its self-declared fields and archive digest but no signature. `backend/scripts/check_backup_freshness.py:93` ranks by that inventory's `generated_at`; line 185 uses it as recovery age.

**Reproduction:** [probe_backup_freshness.py](evidence/ops/probe_backup_freshness.py) creates synthetic archive/checksum/inventory files with a 72-hour-old inventory. The checker exits 2. Changing **only** `generated_at` in the unsigned inventory to now makes the checker exit 0 and print `fresh complete backup ... (0.0h old)`. Archive bytes/hash and object recovery-point/receipt remain unchanged. The stronger [probe_signed_backup_freshness.py](evidence/ops/probe_signed_backup_freshness.py) then generated a real RSA key in an owned temporary keyring and genuinely signed/encrypted an audit fixture using GPG 2.4.7 inside a pinned tool image. GPG's clock was set back 72 hours for key/signature creation; both before and after the sidecar rewrite GPG reported the exact same `GOODSIG`/`VALIDSIG` with the old cryptographic timestamp. Freshness again changed from exit 2 at 72 hours to exit 0 at 0 hours. Private key material was confined to and removed with the owned temporary directory. This verifies the cryptographic-envelope boundary; the plaintext was a clearly labeled fixture, not a PostgreSQL archive, and no restore is claimed.

**Expected versus actual:** Mutating untrusted backup metadata should not establish a newer authenticated recovery point. Actual verification proves only that metadata names the archive's current digest; that one-way hash binding does not authenticate the metadata, its timestamp, object manifest, or key-escrow identities. `generated_at` is inventory capture time, not an independently verified object snapshot or database dump timestamp.

**Impact:** The whole-system RPO alert can be suppressed while the actual recovery point is stale, and recovery instructions/escrow identities can be substituted independently of an authentic database payload. The database GPG authentication itself remains intact.

**Recommendation:** Authenticate a manifest containing the exact archive digest, database recovery timestamp, object recovery point and escrow identities, and verify it before freshness/restore decisions. Calculate the whole-system age from authenticated component recovery times, not solely the time a descriptive inventory was generated.

**Evidence:** [backup-freshness-result.json](evidence/ops/backup-freshness-result.json), [signed-backup-freshness-result.json](evidence/ops/signed-backup-freshness-result.json).

## O26-05 — Low — Mutation gate trusts summary scores that contradict the raw attempts

**Tracks:** 26, 25. **Confidence:** high for validator behavior; no claim that the current report producer emitted a false passing report.

**Prerequisite:** An erroneous, stale, malformed or altered aggregate report with matching target IDs/campaign identity reaches the final mutation gate. Normal current producers may avoid these values, but the purported independent evidence check does not detect them.

**Location:** `.github/scripts/check_mutation_report.py:89` validates raw record count/IDs/campaign but never their verdicts or complete-selection metadata; lines 127–140 trust measured count and score from the summary, including non-finite floats. The checked-in acceptance test in `backend/tests/test_ci_mutation_gate.py` also supplies records without verdicts.

**Reproduction:** Run [probe_evidence_gates.py](evidence/ops/probe_evidence_gates.py). A plan containing one target passes with (a) an `INCONCLUSIVE_TIMEOUT` raw attempt plus a `complete_measured=1, score=100` summary, (b) a `SURVIVED` raw attempt plus score 100, and (c) a `SURVIVED` raw attempt plus JSON `NaN` score. All three exit 0.

**Expected versus actual:** The gate should derive complete measured attempts and kill ratio from the exact raw evidence, reject contradictions, and reject non-finite/out-of-range scores. Actual checks accept the summary's assertion even though the retained raw evidence disproves it; NaN bypasses the numeric threshold comparison.

**Impact:** A reporting/provenance integration failure can turn inconclusive or ineffective mutation testing into a passing assurance result. This is evidence hardening, not a demonstrated bypass by ordinary application users.

**Recommendation:** Recompute measured status and score from raw complete-selection records, verify all summary fields match, and require finite bounded numeric values. Add the contradictory-evidence cases as contract regressions.

**Evidence:** [evidence-gates-result.json](evidence/ops/evidence-gates-result.json).

## O26-06 — Low — SARIF gate reports a passed scan for zero runs or an explicitly failed invocation

**Tracks:** 26, 25. **Confidence:** high for validator behavior; upstream CodeQL action failure remains an independent guard.

**Prerequisite:** An incomplete or failed SARIF document reaches the local gate while the upstream workflow step has not already failed. This was tested only with synthetic reports; it does not establish that the current CodeQL action produces such a green workflow.

**Location:** `.github/scripts/gate_sarif.py:113` accepts an empty `runs` array and skips malformed/missing results; line 147 treats an empty finding list as passed without checking `invocations[].executionSuccessful` or analysis error notifications.

**Reproduction:** The retained evidence-gate probe invokes the CLI with `{"version":"2.1.0","runs":[]}` and with a run containing `executionSuccessful:false` and `results:[]`. Both exit 0 and print `SARIF gate passed: no error/high findings in 1 file(s).`

**Expected versus actual:** Incomplete/failed analysis should produce an inconclusive/rejected result. Actual behavior equates lack of findings with successful analysis.

**Impact:** A scanner-output or workflow integration regression can retain a misleading passed security verdict despite no completed analysis. Ordinary CodeQL action exit failures still fail the workflow, limiting present exploitability.

**Recommendation:** Require at least one structurally valid expected-tool run, reject explicit unsuccessful invocations/error-level execution notifications, and distinguish a successful zero-findings scan from missing or failed analysis.

**Evidence:** [evidence-gates-result.json](evidence/ops/evidence-gates-result.json).


## O26-07 — Medium — Current frontend Docker build fails on malformed Corepack package-manager declaration

**Tracks:** 22, 25. **Confidence:** high, actual current-source Docker build failure.

**Prerequisite:** Build the supplied frontend Dockerfile using its pinned Node image and committed package manifest. No attacker or unusual input is needed.

**Location:** `frontend/package.json:63`; `frontend/Dockerfile:4` enables Corepack and line 6 invokes the shim. The builder's later `pnpm build` at line 18 uses the same declaration.

**Reproduction:** `docker build -t goatfarm-a26-frontend:7245368 -f frontend/Dockerfile frontend` at the audited commit. The exact pinned Node image reaches dependency stage step 5, then exits 1: `Invalid package manager specification in package.json (pnpm@9.15.9+sha512-...==); expected a semver version`. A disposable copy with only `packageManager` changed to `pnpm@9.15.9` lets Corepack run the intended pnpm 9.15.9, exposing the distinct O26-08 failure. Source files were not edited.

**Expected versus actual:** The repository's own pinned build should bootstrap the declared package manager. Actual Corepack rejects the npm-style integrity suffix before package installation begins. A preinstalled local pnpm and successful local Next build do not exercise this path.

**Impact:** Fresh production frontend image builds and release workflows using this Dockerfile are blocked. This does not establish an outage in an already-deployed image.

**Recommendation:** Generate a valid Corepack `packageManager` declaration with the intended version and supported integrity format, and verify an actual clean Docker build in CI. Preserve integrity pinning rather than simply deleting it in the final fix.

**Evidence:** [frontend-container-build.txt](evidence/ops/frontend-container-build.txt), [frontend-container-input-results.json](evidence/ops/frontend-container-input-results.json), [probe_frontend_container_inputs.py](evidence/ops/probe_frontend_container_inputs.py).

## O26-08 — Medium — Frontend dependency layer omits the required local vulnerability patch

**Tracks:** 22, 25. **Confidence:** high, isolated failing case and passing control.

**Prerequisite:** O26-07 is corrected or otherwise bypassed so pnpm 9.15.9 reaches installation. This is a separate, latent build blocker, not the first error in the unmodified current build.

**Location:** `frontend/Dockerfile:5` copies only `package.json` and `pnpm-lock.yaml` before line 6 installs them; `frontend/package.json:78` and `frontend/pnpm-lock.yaml:22` require `patches/braces@3.0.3.patch`. The later `COPY . .` at Dockerfile line 17 occurs in the next stage, after installation must already have succeeded.

**Reproduction:** The retained probe copies exactly the two dependency-stage inputs into an owned temporary directory and corrects only the malformed package-manager value there. In the same pinned Node image, `corepack enable && pnpm --version && pnpm install --frozen-lockfile` reports 9.15.9 and exits 254 with `ENOENT: no such file or directory, open '/app/patches/braces@3.0.3.patch'`. Copying the existing repository `patches/` directory into that same fixture makes installation exit 0. The probe checks that the manifest bind mount exists; temporary files and installed dependencies were removed afterward.

**Expected versus actual:** All lockfile inputs needed for frozen installation should be present in the dependency layer. The required security patch is absent, so correcting O26-07 alone still cannot build the frontend image.

**Impact:** The production image cannot incorporate the committed dependency mitigation because its installation never completes. This finding does not assert that a successfully produced current image silently omitted the patch.

**Recommendation:** Copy the committed patch directory before `pnpm install` and include it in dependency-cache invalidation. Verify a clean Docker installation and the existing patched-dependency checks.

**Evidence:** [frontend-container-missing-patch.txt](evidence/ops/frontend-container-missing-patch.txt), [frontend-container-input-positive-control.txt](evidence/ops/frontend-container-input-positive-control.txt), [frontend-container-input-results.json](evidence/ops/frontend-container-input-results.json).

## O26-09 — Medium — Pinned backend and PostgreSQL images fail the current vulnerability policy

**Tracks:** 25, 22. **Confidence:** high for scanner/package-version and policy-gate results; runtime reachability/exploitation was not established.

**Prerequisite:** Build the current backend Dockerfile or use the current Compose PostgreSQL digest, then scan with the fresh 2026-10-04 vulnerability database and the repository's declared HIGH/CRITICAL, fixable-only policy.

**Location:** `Dockerfile:10` pins the backend Python base and deliberately does not upgrade its OS packages. `docker-compose.yml:54` and `.github/workflows/security.yml:140` pin PostgreSQL. The policy gates are `.github/workflows/security.yml:205` and line 221; `.trivyignore.compose-images` is scoped only to PostgreSQL.

**Reproduction:** Current backend and edge Dockerfiles were built directly from audited commit `7245368fa91333fb39387df789fa4ef9a58dbea3`, without source edits, deployment or registry writes. [scan_container_images.py](evidence/ops/scan_container_images.py) exports immutable local image IDs and scans their tarballs using `aquasec/trivy:0.74.0@sha256:62b1e65e8869bc4b4c6aa4fa2b21595256c7c2f6018a9d9ad61caf87187c1969`. The DB was freshly downloaded at `2026-10-04T22:37:24Z`, with `UpdatedAt=2026-10-04T19:39:34Z`; scans were then offline. All scanned images are Linux ARM64, so this is not evidence for the untested AMD64 variant. Exact image IDs/digests, DB SHA-256, commands, policy-file SHA-256 and all results are retained in [container-scan-manifest.json](evidence/ops/container-scan-manifest.json).

**Policy:** Vulnerability-only scans, HIGH/CRITICAL, `--ignore-unfixed`, exit code 1 for remaining findings. Only the PostgreSQL policy run loads the exact preserved `.trivyignore.compose-images`; backend/edge load no suppression file. Each image also has an unfiltered all-severity/all-fix-status report with no suppressions. The workflow's snakeoil-key exclusion concerns secret scanning; these runs scan vulnerabilities only and make no secret-scan claim.

**Expected versus actual:** The declared gate requires zero unsuppressed fixable HIGH/CRITICAL records. The actual current backend has **20 records (17 HIGH, 3 CRITICAL; 16 distinct advisories)** and exits 1. PostgreSQL still has **8 HIGH records (4 distinct advisories)** after its documented suppressions and exits 1. Current edge exits 0 with zero HIGH/CRITICAL records. The four raw pinned base/service scans are also preserved; base-only findings are not automatically attributed to a final application image that removes build tools. A current full frontend runtime could not be built because of O26-07/08, so its base scan is not represented as a completed runtime scan.

**Impact and limits:** These reproducible failures block the repository's stated security acceptance policy and show stale vulnerable package versions in shipped runtime inputs. Scanner severity is not the audit's application severity: no remote code execution, unauthenticated attack path or exposure of every listed component is established. For example, the Perl 32-bit advisory does not prove applicability to this ARM64 run; source-package matching can also cover optional modules. The confirmed finding is image freshness/policy noncompliance, assessed Medium, rather than 28 independently proven exploitable application vulnerabilities. Existing CI is expected to fail on this evidence; a gate bypass is not claimed.

**Recommendation:** Refresh the affected immutable bases/service digest to reviewed images with the fixes, or remove unneeded components in a reproducible build; rerun scans for both release architectures. Where an advisory is not applicable, document a component-specific, bounded exception with evidence instead of treating a scanner label as exploitability proof. Do not extend broad suppressions merely to make the gate green.

All unsuppressed package/advisory records failing those two runtime gates follow. Fixed versions are the fresh scanner's package-manager recommendations and have not been installed by this audit.

### Current backend image

| Package | Advisory | Installed | Fixed version | Scanner severity |
|---|---|---|---|---|
| `gzip` | CVE-2026-41992 | `1.13-1` | `1.13-1+deb13u1` | HIGH |
| `libpcre2-8-0` | CVE-2026-103111 | `10.46-1~deb13u1` | `10.46-1~deb13u3` | HIGH |
| `libpcre2-8-0` | CVE-2026-86145 | `10.46-1~deb13u1` | `10.46-1~deb13u2` | HIGH |
| `libpcre2-8-0` | CVE-2026-89157 | `10.46-1~deb13u1` | `10.46-1~deb13u2` | HIGH |
| `libpcre2-8-0` | CVE-2026-89161 | `10.46-1~deb13u1` | `10.46-1~deb13u2` | HIGH |
| `libsqlite3-0` | CVE-2026-11822 | `3.46.1-7+deb13u1` | `3.46.1-7+deb13u2` | HIGH |
| `libsqlite3-0` | CVE-2026-11824 | `3.46.1-7+deb13u1` | `3.46.1-7+deb13u2` | HIGH |
| `libssl3t64` | CVE-2026-75804 | `3.5.7-1~deb13u2` | `3.5.7-1~deb13u3` | HIGH |
| `libssl3t64` | CVE-2026-84782 | `3.5.7-1~deb13u2` | `3.5.7-1~deb13u3` | HIGH |
| `openssl` | CVE-2026-75804 | `3.5.7-1~deb13u2` | `3.5.7-1~deb13u3` | HIGH |
| `openssl` | CVE-2026-84782 | `3.5.7-1~deb13u2` | `3.5.7-1~deb13u3` | HIGH |
| `openssl-provider-legacy` | CVE-2026-75804 | `3.5.7-1~deb13u2` | `3.5.7-1~deb13u3` | HIGH |
| `openssl-provider-legacy` | CVE-2026-84782 | `3.5.7-1~deb13u2` | `3.5.7-1~deb13u3` | HIGH |
| `perl-base` | CVE-2026-13221 | `5.40.1-6` | `5.40.1-6+deb13u1` | CRITICAL |
| `perl-base` | CVE-2026-42496 | `5.40.1-6` | `5.40.1-6+deb13u1` | CRITICAL |
| `perl-base` | CVE-2026-8376 | `5.40.1-6` | `5.40.1-6+deb13u1` | CRITICAL |
| `perl-base` | CVE-2026-42497 | `5.40.1-6` | `5.40.1-6+deb13u1` | HIGH |
| `perl-base` | CVE-2026-48962 | `5.40.1-6` | `5.40.1-6+deb13u1` | HIGH |
| `perl-base` | CVE-2026-57432 | `5.40.1-6` | `5.40.1-6+deb13u1` | HIGH |
| `perl-base` | CVE-2026-57433 | `5.40.1-6` | `5.40.1-6+deb13u1` | HIGH |

### Pinned PostgreSQL service image, after repository suppressions

| Package | Advisory | Installed | Fixed version | Scanner severity |
|---|---|---|---|---|
| `libpcre2-8-0` | CVE-2026-103111 | `10.46-1~deb13u1` | `10.46-1~deb13u3` | HIGH |
| `libpcre2-8-0` | CVE-2026-89157 | `10.46-1~deb13u1` | `10.46-1~deb13u2` | HIGH |
| `libssl3t64` | CVE-2026-75804 | `3.5.6-1~deb13u2` | `3.5.7-1~deb13u3` | HIGH |
| `libssl3t64` | CVE-2026-84782 | `3.5.6-1~deb13u2` | `3.5.7-1~deb13u3` | HIGH |
| `openssl` | CVE-2026-75804 | `3.5.6-1~deb13u2` | `3.5.7-1~deb13u3` | HIGH |
| `openssl` | CVE-2026-84782 | `3.5.6-1~deb13u2` | `3.5.7-1~deb13u3` | HIGH |
| `openssl-provider-legacy` | CVE-2026-75804 | `3.5.6-1~deb13u2` | `3.5.7-1~deb13u3` | HIGH |
| `openssl-provider-legacy` | CVE-2026-84782 | `3.5.6-1~deb13u2` | `3.5.7-1~deb13u3` | HIGH |

**Evidence:** [backend-container-build.txt](evidence/ops/backend-container-build.txt), [edge-container-build.txt](evidence/ops/edge-container-build.txt), [container-scan-summary.txt](evidence/ops/container-scan-summary.txt), [container-scan-manifest.json](evidence/ops/container-scan-manifest.json), [complete raw scanner inventory](container-vulnerability-inventory.md), and the individual `trivy-*-raw.json` / `trivy-*-repository-policy.json` reports in [evidence/ops](evidence/ops). No pre-existing app image was used as current-build evidence.
