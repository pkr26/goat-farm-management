# Final backend verification

The complete final pytest collection contains **5,128 unique test cases across 137 files**. It was divided by whole file into two disjoint 2,564-case partitions and run concurrently against separately named disposable PostgreSQL databases. No case was omitted or duplicated. The unchanged application/test/schema/config fingerprints match all 410 recorded files.

**Result: 5,124 passed, 4 skipped, zero failures or errors.** The skips are recorded in the JUnit files; they were not introduced to conceal failures. Each partition exited zero; both owned databases were dropped.

Coverage was recorded with `coverage run --branch --source=app -m pytest`, then combined. The final `coverage report`, XML and JSON commands enforced the existing `pyproject.toml` **92% combined floor**, without a threshold override. Combined coverage is **92.7189%**; line coverage **94.5437%** and branch coverage **85.9931%** are reported separately. Test coverage is not an application quality score.

- [Summary and cleanup/source checks](summary.json)
- [Exact collection, partitions and source SHA-256 manifest](manifest.json)
- [Partition 1 log](part-1.log), [JUnit](part-1.xml), [receipt](receipt-1.json)
- [Partition 2 log](part-2.log), [JUnit](part-2.xml), [receipt](receipt-2.json)
- [Combined report](coverage-report.log), [coverage XML](coverage.xml), [coverage JSON](coverage.json)
- [Partition runner](run-part.py), [aggregation/source/cleanup verifier](aggregate.py)

The two coverage databases and combined database are retained here for reconciliation. Earlier integration results remain under `../prior-integration/` and do not replace this final run. No full fresh mutation campaign, supported-load measurement, real provider call or production deployment is implied by these tests.
