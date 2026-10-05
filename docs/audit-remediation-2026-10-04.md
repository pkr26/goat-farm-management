# Remediation of the 29 audit findings

All 29 findings from the independent 26-track audit were fixed. This summary records the implementation, validation and deployment requirements. Detailed audit evidence is retained locally under `audit_reports/` and excluded from source control.

## Changes

| Finding | Correction |
| --- | --- |
| D26-01 | Validate birth/purchase chronology before mutations and enforce it with a database constraint and legacy-data preflight. |
| D26-02 | Aggregate the entire expense window in SQL instead of truncating at 20,000 rows. |
| D26-03 | Preserve an unspecified flagged screening concern as a finding for human review. |
| D26-04 | Compare feeding and farm-creation dates in the farm's business timezone. |
| D26-05 | Reject malformed specialist positives while preserving the gate observations. |
| D26-06 | Use actual linked death dates and consistent mortality phase boundaries; exclude early sales and culls. |
| D26-07 | Remove disposed breeding-stock book value, stop depreciation on exits and expose the noncash charge in results, explanations and P&L. |
| D26-08 | Transfer seasonal normalization into the base price to preserve fitted monthly levels. |
| D26-09 | Use expense evidence for expense confidence; income does not inflate it. |
| F26-01 | Retain lazy assets across same-build refreshes, serialize cache updates, warm initial imports and provide locale-load recovery. |
| F26-02 | Resolve the fresh worker Telugu default before persisting the language. |
| F26-03 | Treat offsetless backend timestamps as UTC before farm-date conversion. |
| F26-04 | Block Finish while a photo is selected or uploading; provide an explicit discard action. |
| F26-05 | Clear tenant-specific IDs, search and anchors on farm changes while preserving portable filters. |
| F26-06 | Commit formatter language before notifying language-context consumers. |
| F26-07 | Keep one main landmark in the worker shell. |
| F26-08 | Require management permission for screening intake/retake and explain revoked-access responses. |
| F26-09 | Provide Retry for statistics failures and distinguish stale statistics from empty history. |
| O26-01 | Initialize telemetry from each process's settings without constructing API settings in the worker. |
| O26-02 | Expose the worker registry through an authenticated internal metrics listener. |
| O26-03 | Scope release cleanup to the current publishing attempt and exact owned tags/digests. |
| O26-04 | Authenticate complete recovery inventories with an externally trusted GPG signer and use the oldest recovery-component timestamp. |
| O26-05 | Recompute mutation scores from complete raw receipts and reject inconsistent or incomplete evidence. |
| O26-06 | Reject absent, failed or contradictory SARIF analysis evidence. |
| O26-07 | Correct the Corepack package-manager integrity declaration. |
| O26-08 | Include the dependency patch and required typechecking support in the Docker build context. |
| O26-09 | Refresh immutable image bases and exact-hash security packages while preserving vulnerability thresholds. |
| S26-01 | Bound significant numeric farm-selector digits before integer conversion. |
| S26-02 | Persist attributed logout events atomically with actual revocations, without duplicate replay events. |

The build investigation also isolated native `sharp` loading during Next configuration as a prerequisite for the observed AMD64 teardown failure. A bounded subprocess now reads the installed native versions while preserving the decoder safety policy. Both frontend architectures build successfully.

Maintained regression tests remain in the normal backend, frontend and browser suites. Generated API/client models and pinned package metadata remain required contract and build inputs.

## Validation

| Check | Result |
| --- | --- |
| Backend final collection | 5,479 passed; four intentional finance-category skips; no missing or unresolved tests. |
| Backend coverage | 92.47% combined statement/branch coverage against the existing 92% floor. |
| Frontend full suite | 5,293 tests in 328 files passed; all existing coverage thresholds passed. |
| Frontend coverage | Statements 94.60%, branches 91.91%, functions 94.60%, lines 96.82%. |
| Changed executable coverage | Backend 99.42%; frontend 93.98%; existing aggregate and file floors passed. |
| Chromium and WebKit | 210 passed; one intentional Chromium-only-control skip in WebKit; zero retries. |
| Container checks | Eight ARM64/AMD64 image variants passed the existing vulnerability gates and their runtime/configuration checks. |
| Static checks | Ruff formatting/lint, strict mypy, ESLint, TypeScript, actionlint and shell syntax passed. |
| Schema and contracts | Migration roundtrip/drift checks, legacy-data refusal, OpenAPI regeneration and backward compatibility passed. |
| Recovery | Genuine GPG tamper controls and isolated signed backup/freshness/restore passed. |

The initial backend execution encountered explicit PostgreSQL disk-full errors, which prevented fixture cleanup and caused subsequent duplicate-data failures. Six additional failures used outdated test contracts. A clean recheck of 386 tests in 14 complete files verified every affected test and every newly collected test. The final result reconciles exact test IDs across both runs; the original failed evidence remains available locally.

Firefox could not launch on the host before loading a product page. AMD64 images were executed through QEMU. Physical devices, live paid providers, real object storage and production deployment were not exercised. Passing vulnerability gates preserves the existing fixable HIGH/CRITICAL policy and narrow PostgreSQL exceptions; it does not imply zero total vulnerabilities.

## Deployment requirements

- Apply migration `ff6a7b8c9d01`. Reconcile existing purchase-before-birth records from their source records first; the migration refuses invalid legacy data rather than inventing dates.
- Review saved simulation assumptions and rerun forecasts under model `3.4.1`. Older stored result fields default to zero; saved assumptions are not rewritten automatically.
- Review historical terminal flagged screening images with missing findings; the pipeline fixes do not backfill them automatically.
- Deliver the metrics bearer credential to the worker's own secret directory and configure the private scraper on port 9101.
- Provision the trusted signer and public-key-only keyring through `/etc/goatfarm/backup-freshness.env`, then establish a complete signed v2 backup before enabling the updated monitor. Unsigned v1 inventories are rejected.

Operational setup is described in [deployment](deployment.md) and [backup/recovery](backup-recovery.md).
