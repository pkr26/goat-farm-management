# Backend mutation measurements

Generated manifests, attempt logs, coverage maps, and reports are local
campaign artifacts and CI uploads; they are not source files. Generate fresh
inputs before running or previewing a campaign. Historical percentages without
compatible passing baselines and isolated receipts cannot be scored.

`.github/workflows/mutation.yml` now runs actual application mutants (not
only harness self-tests) for pull requests that touch application/tests and
on a weekly schedule. It regenerates a passing full-suite coverage map,
selects at most 25 mutation sites from changed production files (or a
bounded repository sample when no production file is selected), uses
complete test selections, requires every attempt to reach a scored verdict,
and enforces an 80% assertion-kill floor. The plan, provenance, manifest,
raw attempts, and report are retained for 30 days. This bounded gate is not
represented as a whole-manifest score.

For a new campaign on a stable checkout:

1. Generate a manifest with `python mutation/mutate_gen.py`. Each edit carries
   the original source SHA-256 and exact statement bytes. Validation compiles
   the complete edited file, preserving return/await/yield and loop context.
2. Collect a passing isolated coverage baseline with
   `python mutation/mutate_cover.py`. Collection snapshots the checkout, uses
   a unique disposable test database and publishes coverage plus source/test/
   harness/lock provenance only after the clean suite passes. Changed inputs
   or an interrupted publication invalidate the map. This is a full suite;
   allow the usual integration-suite runtime.
3. Preview with `python mutation/mutate_run.py --dry-run`, then execute with
   `python mutation/mutate_run.py --full --workers 2`. Full mode runs every
   selected coverer. Default mode samples and records that limit explicitly.
4. Preview compatible results with `python mutation/mutate_report.py --dry-run`.
   Generate a report only when ready to record the fresh campaign.

Each attempt gets a private source snapshot, process session and database
namespace. The parent reaps that exact database after process exit, including
timeouts where pytest cannot run fixture teardown. Cleanup allows PostgreSQL
45 seconds to finish an in-progress checkpoint; a cleanup failure remains an
infrastructure result and cannot count as a kill. Neither the runner nor
verification helpers rewrite live app files.
The exact selection must pass unmutated under identical resource limits before
its mutant runs. Structured pytest receipts distinguish assertion failures
from setup, collection, usage, internal, no-test and transport errors. Only
assertion failures become `KILLED`; other failures are `INFRA_ERROR`, invalid
edits are `INVALID`, and every timeout is `INCONCLUSIVE_TIMEOUT`.
Each attempt retains the passing baseline's structured pytest receipt, including
when a later attempt reuses that baseline. The final gate verifies that receipt
against the exact selection and rejects duplicate contradictory phase reports.
Regenerate older receipts that retain only the baseline status and digest.

A campaign receipt binds manifest, source, tests, dependency locks, coverage,
coverage provenance and harness configuration, plus executed migration/scripts
and repository contract/deployment inputs read by the tests. The runner captures
manifest, coverage and coverage-provenance bytes once, fingerprints those bytes,
and parses the captured coverage. Replacing and restoring live artifacts during
execution cannot change its test selection. Every mutation must exactly match
the captured manifest; the snapshot uses that manifest and the execution mode
is fixed when the campaign starts. Coverage publication hashes the bytes from
its passing baseline, so a concurrent publication cannot receive that baseline's
provenance. Reports parse and fingerprint the same captured manifest bytes.
Resume accepts compatible terminal results only; errors, uncovered results and
timeouts are retried.
Reports fold the latest compatible attempt per mutant, show incomplete states,
and exclude sampled/module-fallback selections and inconclusive states from
the complete-selection score. Review equivalence separately with evidence.

Verification receipts append as history. Extremes verification folds only its
own run and target IDs; it never reinterprets another run's timeout. All
execution drivers support a preview that starts no runner, process or write.
The final-pass preview is covered by a byte-identical artifact regression.

## The 38 business-domain campaigns

`domain_specs.json` defines reviewed business and database-constraint changes;
`domain_ownership.json` assigns shared source ranges to their business area.
The domain generator combines those specifications with generic AST mutations,
owns each site once, and writes `domain-plan.json` with the 38 target counts.
Declarative ORM table definitions are exercised through reviewed migration
mutations; executable model methods and pure lifecycle/feed rules are included.

From `backend/`, generate and measure a stable checkout:

```sh
.venv/bin/python mutation/mutate_domains.py generate
.venv/bin/python mutation/mutate_cover.py --workers 8
.venv/bin/python mutation/mutate_domains.py run --workers 12 --timeout 7200 > mutation/domain-full.log 2>&1
.venv/bin/python mutation/verify_domain_receipts.py --require-complete --require-measured
```

Set `MUTATION_TEST_ADMIN_URL` consistently for both coverage and execution when
using a separate disposable PostgreSQL server. Its fingerprint is part of the
coverage provenance and campaign identity. The default remains the local test
server on port 5432. Every pytest invocation still receives its own guarded
throwaway database name.

Domain runs execute every measured coverer, supplemented by explicit business
oracles. Import-time and untraced sites execute the complete declared domain
suite. Overlapping selectors execute each concrete test once, with explicit
oracles first. Bounded progress tests exercise operations in a child process and
assert completion plus their business output; the campaign timeout remains an
inconclusive result.

`--semantic-only` selects the reviewed specifications for focused diagnosis;
it does not constitute a complete domain run. `--domains 1,2` restricts the
areas, and `--status SURVIVED INFRA_ERROR INCONCLUSIVE_TIMEOUT` retries measured
problem cases. The report shows every area's attempted and missing counts and
uses only receipts compatible with the current inputs. Changes to tests or
application code require fresh coverage and measurements. Equivalent edits
need separate evidence and cannot be represented as assertion kills.

The streaming verifier validates the exact exhaustive ordered selection and
each concrete passing-baseline item, including whole-file, class and parameter
family expansion. It accepts only clean call-assertion failures as kills,
rejects contradictory or malformed evidence, and keeps infrastructure failures
and timeouts unmeasured. It writes `domain-report.json` and
`domain-verification.json` without loading the complete attempt history into
memory. The completeness flags require every manifest target to have a
verified verdict; a survivor remains a survivor until a new measurement or a
separate source-bound equivalence review establishes its disposition.

New regression checks run early for registration, profile reads and caps,
quarantine overflow and cached facts, camera pixel limits and provider failure
causes. Registration checks also cover the nested transaction callback, using
the outer function's AST ancestry rather than its shared `mutate` name. Test
changes require a new complete passing coverage map and campaign; old results
remain diagnostic history rather than being inherited as fresh kills.
