# Remediation of all 29 independent-audit findings

Source baseline: `7245368fa91333fb39387df789fa4ef9a58dbea3`. Original audit: [26-track independent audit](../independent-26-track-2026-10-04/INDEPENDENT_26_TRACK_AUDIT.md).

All 29 findings are fixed, with regression tests and integrated validation completed as described below. The original audit and failure reproductions remain preserved. This report records validation completed before the remediation commit; repository publication is recorded in Git history. No release or production deployment was performed during validation. The remaining environment limits are Firefox startup on this host and hardware/live-service checks described under scope of assurance.

## Finding-by-finding changes

The detailed scope reports link maintained regression tests and retained execution receipts. The changes cover the original 1 High, 17 Medium and 11 Low findings.

| Finding | Original severity | Implemented correction | Evidence and explanation |
|---|---|---|---|
| D26-01 | Medium | Validate prospective birth dates; add transactional database constraint and legacy-data preflight. | [Details](domain/REMEDIATION.md) |
| D26-02 | Medium | Aggregate the complete expense window in SQL; remove the 20,000-row cost truncation. | [Details](domain/REMEDIATION.md) |
| D26-03 | Medium | Preserve an unspecified flagged concern as a reviewable finding. | [Details](domain/REMEDIATION.md) |
| D26-04 | Medium | Compare farm creation and feeding dates in the farm business timezone. | [Details](domain/REMEDIATION.md) |
| D26-05 | Medium | Reject malformed specialist positives and preserve the relevant gate observations. | [Details](domain/REMEDIATION.md) |
| D26-06 | Medium | Use actual linked death dates and consistent phase boundaries; exclude early sales/culls. | [Details](domain/REMEDIATION.md) |
| D26-07 | Medium | Derecognize disposed breeding-stock book value; stop depreciation on exits; expose the noncash charge in API, explanations and P&L. | [Details](domain/REMEDIATION.md) |
| D26-08 | Medium | Transfer seasonal normalization into the base price, preserving fitted monthly levels. | [Details](domain/REMEDIATION.md) |
| D26-09 | Low | Count expense evidence only; income does not inflate expense confidence. | [Details](domain/REMEDIATION.md) |
| F26-01 | Medium | Keep lazy assets for the same build, serialize cache updates, warm pre-control imports, and offer locale-load recovery. | [Details](frontend/README.md) |
| F26-02 | Medium | Resolve the fresh worker Telugu default before persisting the language. | [Details](frontend/README.md) |
| F26-03 | Low | Interpret offsetless backend timestamps as UTC before farm-date conversion. | [Details](frontend/README.md) |
| F26-04 | Medium | Fence Finish against selected/pending uploads and provide an explicit discard action. | [Details](frontend/README.md) |
| F26-05 | Low | Remove tenant-specific IDs/search/anchors on farm change while retaining portable filters. | [Details](frontend/README.md) |
| F26-06 | Low | Commit formatter language before notifying context consumers. | [Details](frontend/README.md) |
| F26-07 | Low | Keep one primary main landmark in the worker shell. | [Details](frontend/README.md) |
| F26-08 | Low | Gate screening controls on manage permission and explain revoked-access responses. | [Details](frontend/README.md) |
| F26-09 | Low | Show stats failures with Retry and distinguish stale data from empty history. | [Details](frontend/README.md) |
| O26-01 | High | Initialize metrics from process-specific settings; worker calls no longer instantiate API settings. | [Details](ops/REMEDIATION.md) |
| O26-02 | Medium | Expose the worker registry through an authenticated internal scrape listener. | [Details](ops/REMEDIATION.md) |
| O26-03 | Medium | Bind package cleanup to the current publishing attempt and exact owned digests/tags. | [Details](ops/REMEDIATION.md) |
| O26-04 | Medium | Bind recovery metadata to signed archive inventory and verified signer; enforce the oldest recovery component timestamp. | [Details](ops/REMEDIATION.md) |
| O26-05 | Low | Recompute mutation measurements from complete, consistent raw receipts and match summaries against them. | [Details](root/REMEDIATION.md) |
| O26-06 | Low | Reject absent, failed or contradictory SARIF analysis evidence. | [Details](root/REMEDIATION.md) |
| O26-07 | Medium | Correct the Corepack package-manager integrity declaration. | [Details](ops/REMEDIATION.md) |
| O26-08 | Medium | Include the local dependency patch in the Docker install context; include the mutation support needed by production typechecking. | [Details](ops/REMEDIATION.md) |
| O26-09 | Medium | Refresh immutable bases and apply exact hash-pinned security packages in derived runtime images; retain security thresholds. | [Details](ops/REMEDIATION.md) |
| S26-01 | Low | Bound significant numeric selector digits before integer conversion. | [Details](root/REMEDIATION.md) |
| S26-02 | Low | Persist attributed logout events atomically with real revocations, without duplicate replay events. | [Details](root/REMEDIATION.md) |

## Integrated validation

Tests run against owned disposable PostgreSQL databases and a production standalone Next build. Browser tests use zero retries. Synthetic screening providers and storage are used for controlled error/ordering cases; no paid provider or production data is used.

| Check | Result | Receipt |
|---|---|---|
| Complete backend collection | PASS after full-run/affected-file reconciliation: 5,479 passed, four intentional finance-category skips, zero missing or unresolved nodes across the final 5,483-test collection. The clean recheck covers 386 tests in 14 complete files | [Exact-node results](root/backend-reconciled-results.json), [recheck](root/backend-recheck.log), [initial-run diagnosis](root/backend-initial-diagnosis.md) |
| Backend statement and branch coverage | PASS: combined 92.47%, above unchanged 92% floor; statement coverage 94.33%, branch coverage 85.62%. Worker exporter 100% statements/branches | [Coverage gate](root/backend-coverage-final.log), [XML](root/backend-coverage-final.xml), [detailed JSON](root/backend-coverage-final.json) |
| Complete frontend coverage | PASS on final source: 328 files, 5,293 tests; statements 94.60%, branches 91.91%, functions 94.60%, lines 96.82%; all existing floors unchanged and met | [Log](frontend/full-coverage.log), [coverage](frontend/coverage/coverage-summary.json) |
| Chromium desktop/phone/tablet | PASS: 107 tests, zero retries, 261.12 seconds; includes same-build offline Telugu reload without HTTP cache | [Results](root/browser-chromium-results.json), [offline screenshot](root/offline-telugu.png) |
| WebKit desktop/Mobile Safari | PASS: 103 tests, one intentional skip for the Chromium-only CDP cache control, zero retries, 224.83 seconds | [Results](root/browser-webkit-results.json) |
| Firefox app-independent launch | BLOCKED by host: sandbox/graphics failure, 20-second launch timeout before any application page; probe process exited | [Control](root/firefox-launch-control.log) |
| Backend Ruff formatting and lint | PASS: 438 files formatted; all lint checks pass | [Format](root/ruff-format-final.log), [lint](root/ruff-check-final.log) |
| Backend strict mypy | PASS: 156 application/tool, 159 test and 12 mutation-harness source files | [Application](root/mypy-app-final.log), [tests](root/mypy-tests-final.log), [mutation](root/mypy-mutation-final.log) |
| Frontend lint/typecheck/standalone production build | PASS | [Lint](frontend/integrated-lint.log), [types](frontend/integrated-typecheck.log), [build](root/frontend-build.log) |
| Migration full roundtrip and metadata drift | PASS: upgrade head, check, downgrade base, upgrade head, check; plus legacy refusal/rollback regression | [Roundtrip](domain/migration-roundtrip.log), [domain details](domain/REMEDIATION.md) |
| OpenAPI and generated client | PASS: additive optional fields, regenerated Orval models, backward compatibility against original HEAD | [Compatibility](root/openapi-compat.log), [generation](root/orval.log) |
| Changed frontend executable coverage | PASS: 78/83 changed executable lines (93.98%), above 85%; all changed-file floors met. Uses the unchanged CI evaluator with working-tree diff discovery because changes are uncommitted | [Receipt](root/frontend-changed-coverage.json), [local discovery adapter](root/check_worktree_coverage.py) |
| Changed backend executable coverage | PASS: 170/171 changed executable lines (99.42%), above 85%; all changed-file floors met | [Receipt](root/backend-changed-coverage.json) |
| Frontend cold-route JavaScript budgets | PASS: login 447,381 gzip bytes / 465,920; simulation 530,207 / 547,840 | [Receipt](root/route-js-budget.log) |
| Frontend dependency audit/local patch | PASS under existing policy: zero unmitigated, one locally patched advisory; 19 depth-guard + 4 ordinary-pattern controls | [Audit](root/frontend-dependency-audit.log), [patch checks](root/frontend-dependency-check.log) |
| Independent gate review and genuine producer compatibility | PASS: 94 Python contracts plus 22 Vitest mutation-harness contracts | [Peer review](frontend/peer-review-root.md) |
| Final focused operations suite | PASS: 213 tests across metrics, recovery, deployment, release and security workflow contracts | [Receipt](ops/accepted-targeted-tests.log) |
| Real isolated signed backup/restore | PASS: capture, pg_dump, sign/encrypt, manifest verification, freshness, restore and SQL verification (29 synthetic rows, ID sum 435); nonempty target refused | [Receipt](ops/real-backup-restore.json) |
| Worker metrics endpoint supplement | PASS: three direct listener/authentication/lifecycle controls; exporter 100% statements/branches in a separate coverage capture | [Operations details](ops/REMEDIATION.md) |
| Container vulnerability policy | PASS for all six remediated runtime images: ARM64 and AMD64 backend/frontend/derived PostgreSQL. Original policy preserved; narrow PostgreSQL gosu exceptions retained and obsolete distro exceptions removed. Raw unfixed findings remain visible | [Backend/PostgreSQL manifest](ops/container-scan-manifest.json), [final frontend manifest](ops/frontend-final-scan-manifest.json) |
| Unchanged edge image supplement | PASS: ARM64 and AMD64 builds, fresh vulnerability policy gates, SPDX SBOMs and `nginx -t` controls | [Separate manifest](ops/edge-supplement-scan-manifest.json) |
| Frontend native-dependency build probe | PASS: bounded subprocess probing retains the decoder policy and fixes the reduced build failure. 37 focused regressions and independent review pass. Both final images build, serve health checks and contain sharp 0.35.4 / libvips 8.18.6 / libheif 1.23.2 | [Diagnosis](frontend/amd64-build-diagnosis.md), [peer review](domain/PEER_REVIEW_BUILD_GUARD.md), [builds](ops/frontend-final-builds.json), [runtime](ops/frontend-final-runtime.json) |
| Original audit preservation | PASS: all 397 retained artifacts match the original checksum manifest | [Integrity](root/original-audit-integrity.json) |

Focused tests overlap the complete suites and are not added as unique totals. Retained failed iterations are explained in the detailed reports and are not counted as passing results. The full Firefox product suite was not attempted again after the independent launch control reproduced the existing host limitation. AMD64 images were built and executed under QEMU on this ARM64 host; physical AMD64 hardware was not used.

The first backend execution ran for 51m53s and encountered explicit PostgreSQL disk-full errors while diagnostic build cores consumed Docker storage. Failed fixture truncation then caused duplicate-data failures. Six additional failures came from test contracts collected before their final updates. All 45 affected unique nodes and all 25 newly collected nodes are covered by the clean 14-file recheck (386 passed in 72.78 seconds). The two mutation-harness infrastructure symptoms occurred during the same database outage; their original child exception messages were not retained, so their precise cause remains an inference. The entire harness module passes in the recheck. [Independent diagnosis and reconciliation](root/backend-initial-diagnosis.md) retain that distinction.

The final backend result combines the preserved full run and complete-file recheck by exact test ID, including teardown-only errors. Coverage combines their two raw data files and enforces the original global floor afterward; the focused capture alone has no global-coverage claim. The changed-coverage evaluator is unchanged. Local working-tree discovery includes new source files, and the backend source root matches the combined XML's `app/...` paths. Initial command/path mistakes are retained as diagnostics and are not reported as successful gates.

## Compatibility and rollout

- Apply migration `ff6a7b8c9d01` through the normal migration service. It rejects existing rows whose purchase date precedes their effective birth date. Reconcile such rows from source records before upgrading; the migration never invents historical dates. Upgrade/downgrade/metadata-drift and transactional legacy-refusal controls are included.
- Saved simulation assumptions are not rewritten automatically. Review and re-calibrate previously saved cost, mortality and seasonal assumptions, then rerun forecasts under model `3.4.1` before relying on the corrected estimates.
- Existing terminal screening images with missing findings are not backfilled by the pipeline fixes. Review any affected historical flagged images and reconcile their findings; new or retried processing follows the corrected evidence-preservation logic.
- New simulation fields default to zero for older stored results. OpenAPI and generated frontend contracts were regenerated and checked against the immutable original schema. Model version becomes `3.4.1`. The model now exposes disposed breeding-stock book value as a separate noncash charge before EBIT and tax; the full acquisition cost remains a cash outflow in the purchase month. Foundation stock retains the existing month-zero policy.
- Worker metrics need the existing metrics bearer credential delivered to the worker and an internal scrape target on port 9101. Without the token the worker opens no scrape socket. Updated Compose and Prometheus examples document the setup.
- Unsigned schema-v1 recovery inventories are rejected by the new verifier. Before enabling the updated monitor, provision `/etc/goatfarm/backup-freshness.env` with the independently verified signer and readable public-key-only GPG keyring, and establish a complete signed v2 backup. Preserve historical archives for separately reviewed provenance recovery using their original component times. Recovery inventory and signer requirements changed to close unsigned-metadata tampering. Follow [updated backup/recovery documentation](../../docs/backup-recovery.md) and the operations report when producing the next signed recovery set. Do not relabel an old object recovery point as new merely by updating metadata.
- Container changes preserve the PostgreSQL major/distro/libc line and use immutable base/package bytes. No existing database volume was migrated by this task. The retained, narrow PostgreSQL executable exceptions are reported separately from the vulnerability-policy outcome.

## Scope of assurance

The 29 audit findings are the scope of this remediation. Passing tests do not establish that the entire project has no remaining defects. A new full independent 26-track audit, production restore drill, live provider accuracy evaluation and physical tablet/camera/storage-eviction tests are separate activities. Relevant environment limitations and any failed diagnostic runs remain in the detailed evidence rather than being erased.

The final [validation summary](root/validation-summary.json), [source inventory](root/source-manifest.json), [artifact checksums](root/artifact-sha256.json) and [report integrity check](root/report-integrity.json) make the result reviewable. [Root cleanup](root/cleanup-final.json) confirms disposable databases and browser/API listeners are gone; [operations cleanup](ops/cleanup-final.json) records removal of owned diagnostic containers and temporary scanner material. Accepted local images remain available for review. No global Docker prune or production data change was performed.
