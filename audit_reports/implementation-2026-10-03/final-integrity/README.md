# Independent final integrity check

The [summary](summary.json) preserves the independent read-only reconciliation
of the final backend evidence and repository inclusion set. No application
source was changed and no passing suite was rerun. The initial hygiene review
covered **1,671 Git tracked/nonignored files**; the reproducible verifier reports
its current scope separately because additional final receipts can be added.

The checks compare all **97 historical migration modules** byte-for-byte with
`b285645` (the original database audit also records 97), require exactly four
forward migrations and the sole `f8e2f6a0c5d3` head, match all **63 original audit
bundle files** against the preserved byte-size/SHA-256 manifest, and reconcile
all **410 final backend source hashes**. The original manifest excludes itself;
this is a comparison with that preserved manifest, not an independently signed
archive assertion.

The final backend collection contains **5,128 unique cases** in 137 test files.
Two disjoint whole-file partitions cover the complete collection; their JUnit
receipts reconcile to **5,124 passed, four skipped and no failures or errors**.
The saved coverage JSON reports **92.718882718066% combined**, separately from
line and branch coverage, against the unchanged 92% combined floor. A read-only
local PostgreSQL query confirms the two explicitly named partition databases
are absent. It does not enumerate unrelated databases or read application data.

Credential checks scan included files for common JWT, private-key, provider-key,
credential URL and browser-auth patterns. JSON evidence is checked for nonempty
credential/cookie/storage fields, and filenames are checked for raw traces and
auth-state artifacts. No matched values are printed. The sole initial
private-key-header candidate is a 64-byte invalid negative-test fixture.
**These bounded heuristic checks do not prove that every possible secret is
absent.** Ignored local secrets and traces held outside the repository inclusion
set are outside this certification scope. The verifier receipt directory is
excluded from the inclusion scan to keep reruns noncircular.

Run from the repository root with the existing backend environment and local
PostgreSQL server:

```sh
backend/.venv/bin/python audit_reports/implementation-2026-10-03/final-integrity/verify.py
```

[verify.py](verify.py) performs read-only commands/queries and prints JSON. It
does not write its output file, modify code or database rows, or execute tests.
A mismatch exits nonzero. Later source/documentation changes can legitimately
invalidate the frozen backend hashes; those changes require fresh evidence or
an explicitly scoped explanation, rather than weakening the comparison.

This receipt does not imply production deployment, real provider delivery,
production load/restart soak, private-CA recovery, domain accuracy or field
validation. Those remain governed by their separately recorded evidence.

## Handoff reconciliation

[The final handoff receipt](handoff-summary.json) reran the same read-only reconciliation after the final browser gates and top-level documentation updates. All checks pass; [summary.json](summary.json) now points to this current receipt, and [the earlier receipt](prior-summary.json) remains separately preserved. The final browser snapshot independently verifies all 964 frontend source/test/config entries in `../final-verification/summary.json`.

[The 1,482-file current project inventory](current-file-inventory.csv) records bytes and SHA-256 for Git-visible project files, excluding the implementation-evidence directory to avoid circular manifests. [Its scope and digest](current-file-inventory.json) distinguish this mechanical identity inventory from the original semantic audit. The task-generated synthetic E2E credential-state file was removed after every browser gate finished; the normal global setup creates a fresh one on a future run.
