# File coverage and audit method — 2026-10-03

The [CSV ledger](/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/01-file-coverage.csv) has exactly one row for each of the **1,343 initially tracked files** at commit `b285645b2a93709aa7294fc4698ee0f25839b13e`. Newly produced audit documents, local caches, installed dependencies and transient E2E credentials are excluded. Application files were not edited.

Inventory totals: **37,448,565 bytes** and **596,278 newline characters**. The newline count is a mechanical inventory measure, not a semantic complexity or test coverage metric; binary assets can also contain newline bytes. SHA-256 values were computed from the unchanged tracked files and checked against the original inventory byte lengths.

## Reading depth

Full semantic review means the assigned reviewer displayed and read the complete source, including large files in sequential or overlapping chunks. Reviewers independently examined frontend/UI/state, domain/backend, database/migrations and authentication/platform code. The strongest confirmed depth is recorded when reviewer scopes overlap. Searches, AST parsing, executing tests and generated-client parity do not count as a complete manual semantic read.

| Review depth | Tracked files |
|---|---:|
| asset metadata review | 3 |
| complete generated structural review | 403 |
| complete parity sampled language semantics | 2 |
| complete semantic source review | 436 |
| empty file no semantic content | 3 |
| inventory only | 14 |
| mechanical review without full semantic read | 13 |
| mechanical search without full semantic read | 4 |
| sampled semantic review | 7 |
| suite execution without full semantic read | 457 |
| visual asset review | 1 |

## Scope categories

| Category | Tracked files | Complete semantic source reviews |
|---|---:|---:|
| backend application | 124 | 122 |
| binary or vector asset | 5 | 1 |
| build tooling or repository configuration | 23 | 23 |
| database migration | 99 | 99 |
| dependency manifest lock or pin | 7 | 5 |
| deployment configuration | 10 | 10 |
| documentation | 18 | 3 |
| frontend application | 127 | 127 |
| generated api client | 404 | 1 |
| localization catalog | 2 | 0 |
| mutation harness and artifacts | 26 | 16 |
| openapi contract | 1 | 0 |
| operational script or template | 23 | 23 |
| test source and support | 474 | 6 |

All **251 application-source paths** (backend/app and authored frontend application scope) have complete semantic source review or zero-byte files with no semantic content. This count excludes localization catalogs, generated API clients, assets, tests, operational scripts, configuration and documentation.

Generated API files received complete AST/structural or endpoint-pattern inspection, with contract parity checks; one generated model was additionally read manually. Locale catalogs received complete key, empty-value and interpolation checks, with sampled linguistic review. The report does not claim exhaustive Telugu translation validation.

## Mechanical and runtime evidence

- Every initially tracked file received an inventory row; all Python files parsed successfully using Python 3.13.15 and all JSON files were validated. Ruff passed. Strict mypy for app/scripts/mutation passed; the test typing ratchet still permits 1,778 existing errors.
- The isolated backend suite passed 4,978 tests with 4 skipped; the frontend Vitest suite passed 5,082 tests in 314 files. Frontend typecheck, lint and production build passed.
- Live Chromium/Mobile Chrome E2E checks finished with 45 passing and 6 failing tests. Failures and stale selector/status assertions are discussed in the main report; executing a suite does not prove every line of each participating file ran.
- The entire migration chain upgraded an isolated PostgreSQL database to its sole head, and Alembic reported no model/schema changes. Offline SQL-rendering limitations were found. A production restore drill and load test were not performed.
- A fresh OpenAPI export matched 101 paths and 235 schemas. Generated model inspection checked 231 interfaces; both locale catalogs have 2,797 matching keys.
- Historical mutation results were independently recomputed using the latest result per mutant ID. Those campaigns were not rerun, and their scores are not current runtime line-coverage measurements.
- Dependency advisory scans found failing release gates. The audit distinguishes installed-package advisories from proven reachable application vulnerabilities.

## Remaining depth limitations

Test and fixture sources were executed and selectively read rather than every assertion being manually examined. Historical audit/report prose, bulk mutation outputs, dependency locks and generated code received inventory or mechanical inspection with the specific depth recorded per file. No claim is made that every historical artifact was read word for word.

No nonempty application, operational script, migration, build/deployment configuration or mutation-harness source gaps remain after supplementary reads. This does not upgrade the review depth of tests, language catalogs, generated code, machine data or historical documentation.

A file review cannot establish absence of defects. Real-farm task observation, assistive-technology testing, low-end devices, disconnected refresh/restart behavior, live model-provider evaluation, production volume/load and disaster recovery remain separate validation work. The score in the main report is an evidence-based engineering judgment, not a certification.
