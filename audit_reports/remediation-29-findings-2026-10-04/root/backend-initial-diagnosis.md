# Initial integrated backend run: evidence and reconciliation scope

Independent read-only diagnosis on 2026-10-04. Sources: [initial log](backend-full.log) and [JUnit](backend-full.xml), whose SHA-256 is `acea8d7e9be491088b435cef8114f3555acbda2141964aed392700a89e8e29fc`. No application files or databases were changed by this review. The failed initial outcome remains below; the completed reconciliation is recorded at the end.

The command reported **5,423 passed, 31 failed, 4 skipped and 36 errors in 51m53s**. JUnit contains **5,458 unique node IDs**, not the later collection count: 5,409 have no failure/error/skip, 14 have teardown errors only, 22 have both call failures and teardown errors, nine have call failures only, and four are skipped. The XML's 5,480 records count those 22 call/teardown pairs twice. Pytest's 5,423 passed includes 14 calls whose teardown failed; they require reconciliation too.

## Complete failure classification

| Initial failure messages | Count | Interpretation and required verification |
| --- | ---: | --- |
| `No space left on device` | 11 | Explicit PostgreSQL disk-full failures: maintenance progress (9), metrics (2). Rerun after restoring storage. |
| `could not write init file` | 3 | PostgreSQL initialization failures in maintenance subprocess, migration preflight and monetized-cull migration, during the same disk-full interval. Rerun all three modules. |
| `ix_users_email` duplicate constraint / email already registered | 9 | Six maintenance fixtures, two logic registrations and one notification-delivery registration. Failed preceding truncation left fixture identities behind; these tests need a clean database. |
| Mutation `INFRA_ERROR` / missing database-created marker | 2 | Direct evidence supports infrastructure/non-assertion failures; exact child exception text was not retained. See bounded conclusions below. |
| Old release receipt filename assertion | 1 | `test_infra_recovery_hardening` still expected `goatfarm-release-owned.jsonl`; final contract includes run/attempt identity. Verify the corrected exact contract. |
| Missing `GITHUB_RUN_ID` in release cleanup fixture | 4 | The running tests supplied the older environment contract. Verify final ownership controls with complete run/attempt fixture data. |
| Missing three frozen worker metrics fields | 1 | Defaults table omitted token, token-file and enabled fields. The exact expected fields were added; [scoped five-test receipt](../domain/settings-defaults.log) passed. Include the whole module in reconciliation. |

All **36 error events** explicitly contain PostgreSQL `DiskFullError` / `No space left on device` during `TRUNCATE TABLE`: logic 3, maintenance progress 22, metrics 9, monetized-cull migration 1, notification-delivery integrity 1. The first is teardown of `test_logic.py::test_purchase_batch_zero_age_months_sets_dob_to_batch_date`. The affected module counts, including both kinds of event, are:

| Module (`backend/tests/`) | Failures | Teardown errors |
| --- | ---: | ---: |
| `test_infra_recovery_hardening.py` | 1 | 0 |
| `test_logic.py` | 2 | 3 |
| `test_maintenance_progress.py` | 16 | 22 |
| `test_metrics.py` | 2 | 9 |
| `test_migration_env_preflight.py` | 1 | 0 |
| `test_monetized_cull_migration.py` | 1 | 1 |
| `test_mutation_harness.py` | 2 | 0 |
| `test_notification_delivery_integrity.py` | 1 | 1 |
| `test_release_cleanup_ownership.py` | 4 | 0 |
| `test_settings_defaults.py` | 1 | 0 |
| **Total** | **31** | **36** |

## Independent inspection of the two mutation failures

`test_two_concurrent_mutants_have_clean_baselines_and_exclusive_snapshots` returned `['INFRA_ERROR', 'INFRA_ERROR']`, not two kills. In `mutation/mutate_run.py`, an infrastructure failure in the exact clean baseline or mandatory attempt-database cleanup prevents a kill. The inspected retained fixture directory contained the tiny source/tests, coverage provenance and manifest, but no stdout/result records. Its private attempt workspaces are deleted by `TemporaryDirectory`, and `runner.close()` removes its snapshot. Therefore this review cannot recover which infrastructure operation failed for either concurrent attempt.

`test_killed_pytest_attempt_reaps_its_real_disposable_database` failed because `attempt-database.txt` was absent. Before temporary-directory cleanup, the reviewer read this retained child receipt:

```json
{"exit_code": 1, "collected": 1, "reports": [{"nodeid": "tests/test_hang.py::test_hang", "when": "setup", "outcome": "passed", "assertion": false}, {"nodeid": "tests/test_hang.py::test_hang", "when": "call", "outcome": "failed", "assertion": false}, {"nodeid": "tests/test_hang.py::test_hang", "when": "teardown", "outcome": "passed", "assertion": false}], "collection_errors": []}
```

Original receipt path: `/private/var/folders/yy/8vxx571s72vbb5jg4zt6v2fm0000gn/T/pytest-of-pradeepreddy/pytest-24790/test_killed_pytest_attempt_rea0/project/backend/receipt-fc7826de4003468da4d7d0539b551ee0.json`. The concurrent fixture was under the same run's `test_two_concurrent_mutants_ha0/`. These temporary paths were removed before the review finished; the observed receipt body is preserved above.

The receipt establishes a collected test with a **non-assertion call failure and exit 1**, before the database-created marker; it does not establish a timeout or successful cleanup test. Its format contains no exception message. Both mutation failures are consistent with the adjacent PostgreSQL disk/init-file failures, including the runner's mandatory database-cleanup dependency even for tiny pure-Python mutants. The specific disk/init-file cause for these two is an inference, not a directly retained child traceback. Neither is a valid mutation kill or a passing acceptance result. A clean rerun of the entire harness module is required.

## Completed reconciliation, independently checked

The coordinator's [recheck log](backend-recheck.log) and [recheck JUnit](backend-recheck.xml) report **386 passed in 72.78 seconds**, with no failures, errors or skips. The [run receipt](backend-recheck-run.json) identifies the explicit disposable database, command and 14 complete test modules.

The reviewer independently parsed both JUnit files and the [final collection](backend-final-collection.log), preserved failure/error precedence for the initial duplicate records, and compared every node with [the reconciliation artifact](backend-reconciled-results.json). This verified:

- All **45 distinct initial problem nodes**, including the 14 teardown-only errors and both mutation-harness failures, have passing recheck receipts.
- All **25 newly collected nodes** have passing recheck receipts: six mutation-gate, eight security-workflow, ten worker-metrics and one deployment-artifact case.
- Each of the **14 rechecked modules** includes its entire final collection, totaling 386 tests.
- The final union has exactly **5,483 nodes: 5,479 passed and four skipped**, with zero missing, extra or unresolved nodes.
- The four skips are identical to the initial receipt: manual income/expense creation for the system-generated `ANIMAL_SALE` and `ANIMAL_PURCHASE` categories in `test_finance_extended.py`, all with reason `system-generated categories reject manual rows`.

The passing harness rerun resolves acceptance for both mutation tests; it does not retroactively recover their missing original exception text or change the original cause-attribution limit above.

The recheck used scoped coverage capture, then the coordinator combined the full-run and recheck data. The [final coverage JSON](backend-coverage-final.json) reports **92.47138975006698%**, above the unchanged 92% global floor; [coverage log](backend-coverage-final.log). The [changed-code receipt](backend-changed-coverage.json) reports **170/171 executable changed backend lines covered (99.42%)**, and the worker metrics exporter has 100% coverage in the final report. Scoped `--cov-fail-under=0` was a capture setting, not a replacement for the separately enforced global floor. This is a reconciled complete-suite result with retained initial failures, not a claim that the original single invocation was green.
