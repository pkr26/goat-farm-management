# Independent verification of commit ad2f616

Target: `ad2f616380fa3df21cc13f32355e086316326398`. Audit date: 4 October 2026, America/Phoenix. The initial checkout was clean at `e3054d7`; its application, tests, workflows and deployment inputs matched the target commit. That later commit only changed audit-artifact retention and documentation.

## Findings

The independent review reproduced remaining gaps in **five of the original 29 findings**: D26-05, F26-01, O26-03, O26-05 and O26-06. Those gaps are fixed in this change. The other 24 original fixes held within the checks below. Two additional defects, invalid timestamp normalization and a lost cross-tab logout signal, were also reproduced and fixed.

Three separate reviewers inspected domain, frontend and operations code. The coordinating reviewer covered authentication, evidence gates and integrated verification. Reviewers used the original issue descriptions as acceptance requirements and challenged the implementation with new boundary tests, controlled failure injection and peer review. Previous remediation test receipts were not counted as fresh passes.

The final results below distinguish complete suite verification, independent probes and remaining environmental limits.

| Original ID | Independent result | Checks performed |
| --- | --- | --- |
| D26-01 | Verified | Sale chronology against acquisition and recorded health facts; rejected writes leave the animal and sale ledger unchanged; database constraint and migration refusal. |
| D26-02 | Verified | Complete-window aggregation with 20,002 structured expense purchases, weighted feed prices, void/window/farm exclusion and category totals. |
| D26-03 | Verified | A flagged gate with no specific observation retains a neutral concern through negative or malformed specialist processing and human review. |
| D26-04 | Verified | First local day in western/eastern zones and historical creation-day backfill across a New York DST transition; one stock debit. |
| D26-05 | Residual fixed | Missing, null, false, zero, empty-string and object assessments cannot erase an eye concern while another specialist succeeds. |
| D26-06 | Verified | Actual linked death dates, mixed linked/legacy reporting, neonatal/post-weaning boundaries, age-91 exclusion and early-sale censoring. |
| D26-07 | Verified | Disposal/depreciation, tax/DSCR controls and 12 independent 60-month acquisition/book-value conservation trajectories. |
| D26-08 | Verified | Unequal monthly sale samples, schema round-trip and reconstruction through the actual market-price function preserve observed levels. |
| D26-09 | Verified | Income cannot inflate expense confidence; structured feed, labour, veterinary and other categories count their own evidence. |
| F26-01 | Residual fixed | Overlapping navigations, separate worker instances, absent Web Locks and an exact v3-to-v4 upgrade; real cold-cache offline Telugu reload. |
| F26-02 | Verified | Fresh worker/login/offline starts default to Telugu without catalog preload; preferences and similarly named manager routes are respected. |
| F26-03 | Verified | UTC-naive timestamps across farm date/year boundaries and DST under two browser-process timezones. |
| F26-04 | Verified | Same-batch selection and active second upload block Finish even when a test bypasses the disabled DOM control; duplicate Finish is fenced. |
| F26-05 | Verified | Duplicate/encoded tenant parameters, anchors, return destinations and route transitions; integrated farm-switch journey. |
| F26-06 | Verified | Slow stale Telugu completion cannot reverse English context, formatter output, document language or persistence. |
| F26-07 | Verified | One main landmark, no nested main and fresh offline-worker axe checks. |
| F26-08 | Verified | Revoked management permission removes open capture/intake; read-only guidance remains and backend mutations retain authorization. |
| F26-09 | Verified | Cold outage, retry, stale populated/empty results and unavailable-versus-empty history are distinguished. |
| O26-01 | Verified | Production-shaped worker success/error cycles with telemetry enabled and disabled use worker settings without API-only configuration. |
| O26-02 | Verified | Separate-process authenticated private scrape exposes worker metrics; listener authentication, lifecycle and configuration controls. |
| O26-03 | Residual fixed | Cleanup ownership still holds; immutable-release preflight rejects failed GitHub/GHCR inspections and checks drafts with an authorized token before any publication. |
| O26-04 | Verified | Genuine signed inventory tampering, wrong signer, stale object/database components and an isolated encrypted backup/freshness/restore drill. |
| O26-05 | Residual fixed | Actual passing backend baseline receipts, complete frontend test inventories, required hook outcomes, consistent run reasons and unique pytest phase reports. |
| O26-06 | Residual fixed | Empty/failed SARIF and contradictory CodeQL termination evidence reject; a valid clean zero-exit SARIF control passes. |
| O26-07 | Verified | Fresh frontend dependency stage resolves the integrity-qualified Corepack declaration to the pinned pnpm version. |
| O26-08 | Verified | Clean dependency stage contains and exercises the local braces patch; complete final frontend runtime image builds. |
| O26-09 | Verified under existing policy | Fresh ARM64 backend/frontend/PostgreSQL/edge gates preserve fixable HIGH/CRITICAL thresholds and existing scoped PostgreSQL exceptions. |
| S26-01 | Verified | Oversized significant/leading-zero selectors, authorization boundaries and large in-range selectors return bounded client responses. |
| S26-02 | Verified | Concurrent exact/cookie/bearer logout produces one attributed transition; replay, ambiguous identities and failed commit do not manufacture events. |

## Corrections made in this audit

1. **Screening evidence:** require an explicit specialist `conditions` list. Ill-formed negative assessments follow the controlled error path and preserve their region's gate concern. A valid empty list still means no specialist condition was observed.
2. **Offline cache concurrency:** serialize dependency staging, shell publication and pruning across worker instances using origin-wide Web Locks. Cache v4 isolates older v3 writers; old immutable dependencies remain available to already-open documents. Without shared locks, chunks are conservatively retained.
3. **Release safety:** replace inspection-error-as-absence logic with completed paginated GitHub reads and authenticated GHCR manifest checks. Existing tags, auth/network errors, malformed responses and server failures stop publication. The image job needs `contents: write` because successful reader-only release listings omit drafts; this permission is used for GET inspection after the tagged-main CI/Security prerequisites. Repository publication remains in the later release job. See [GitHub’s release-list permissions](https://docs.github.com/en/rest/releases/releases#list-releases). Tests use local doubles; no release or registry write occurred.
4. **Mutation evidence:** retain and independently validate the actual clean backend receipt, compare complete frontend test-name inventories, require hook evidence, and reject inconsistent reasons/errors or duplicate pytest phases. Genuine backend fail-fast and frontend producer receipts still pass.
5. **Security-scan evidence:** a success Boolean cannot override a CodeQL nonzero exit or signal termination. These are rejected as incomplete/failed analysis, separately from reported findings. CodeQL's normal analysis success exit is zero; see [GitHub's exit-code reference](https://docs.github.com/en/code-security/reference/code-scanning/codeql/codeql-cli/exit-codes).
6. **Additional date-display defect:** reject impossible source calendars before JavaScript normalizes full timestamps. February 30 and invalid leap days now display the existing invalid-input marker.
7. **Additional authentication defect:** commit the user reference and React state together. A logout storage event arriving during the authenticated layout commit can no longer be discarded before passive effects update the reference. A deterministic regression fails the original source and passes the correction; no timeout was increased.

Maintained regressions are in the normal backend/frontend suites, including new independent domain, security, evidence-gate and release-preflight modules. The mutation READMEs describe the strengthened receipt contract. Older backend receipts containing only a baseline label/digest must be regenerated.

## Final verification

Completed checks:

- Backend: **5,557 passed, 4 intentionally skipped**, covering the complete final 5,561-node collection. Combined line/branch coverage is **92.49%**, passing the unchanged 92% floor. Three isolated database shards covered 5,559 original nodes; a late draft-visibility fix changed only the workflow and its tests. All five files reading that workflow were rerun on final source: 225 earlier cases were replaced by 227 final cases. Independent JUnit reconciliation proves no missing, additional or duplicate nodes, unchanged application hashes and stable final-source hashes for the replacement run. The original coordinator exits 1 solely because its source-change detector correctly reported those two late changes; all three child pytest runs exit 0. Coverage combines exactly the three raw line/arc sets, independently verified rather than averaged. The four skips are existing system-generated sale/purchase category combinations excluded from manual-row parameterization.
- Frontend: **5,301 passed** across 328 files; coverage is 94.59% statements, 91.91% branches, 94.60% functions and 96.82% lines. All configured aggregate and scoped floors passed.
- Chromium: **107 browser journeys passed**, with retries disabled, across desktop, phone and tablet projects against the final production build and a migrated disposable PostgreSQL database.
- WebKit: **103 passed, 1 expected skip**, covering all 104 configured nodes across six fresh-process batches of at most 25 cases, with retries disabled and unchanged timeouts/assertions. This includes desktop and Mobile Safari. Exact selection and runtime node-ID reconciliation verify complete, disjoint coverage. The skip requires Chromium-only CDP HTTP-cache clearing.
- Backend lint and formatting, application/test mypy, frontend lint/type checks and production build passed. Dependency verification/audit and route budgets passed.
- Final-source release peer review: **227 tests passed** across all five test files reading the workflow.
- OpenAPI parity matched all 108 paths and 125 operations.
- Four ARM64 runtime inputs passed the existing container vulnerability policy; real encrypted-backup/recovery and private-worker metrics probes also passed.

Focused results include 259 domain tests, 154 evidence/harness tests, 19 security tests and 23 real frontend mutation-harness contracts. These overlap larger suites and must not be added to unique totals. The separate cold-cache worker checks also exercised final service-worker bytes, native Web Locks, Telugu default, offline reload and axe with zero violations.

## Evidence and limits

Detailed reviewer reports, before/after probes, commands, source manifests, raw logs, JUnit, coverage and screenshots are retained locally under `audit_reports/independent-ad2f616-2026-10-04/`, which the repository intentionally excludes from source control. This maintained report is the portable summary.

This change includes the fixes and regressions verified above. Audit-owned temporary servers, databases, containers, image tags, keyrings and scanner scratch were cleaned up; the evidence and production build remain available for review. No release or production deployment was performed during the audit.

Earlier runs performed while corrections were changing are retained as superseded diagnostics. One complete frontend run exposed the real logout race; its focused pass did not dismiss that failure. An interrupted backend process continued running and collided with a reused disposable test database; it was terminated, the owned databases removed, and final verification restarted with new unique database names. None of those incomplete/failed runs count as final passes.

Two complete WebKit runs each retained one initial-login navigation/teardown timeout after 45 accessibility cases. A fresh isolated case passed, and an application-independent static localhost control using the real axe harness reproduced the same failure after 45 cases: axe creates/closes a second page per case, reaching this Mac's native 90-animation-thread limit. No application requests or service workers participated in that reproduction. A simple 50-context single-page control passed. The bounded-process validation above works around this measured host/harness limitation; it does not relabel either earlier full run as passing. Detailed traces, process samples and selection reconciliation remain in the evidence.

Firefox's fresh application-independent launch control timed out before loading a page with host sandbox/graphics errors. AMD64 images were not rebuilt in this audit. Container passes mean the existing **fixable** HIGH/CRITICAL policy passed, not that raw vulnerability reports are empty; raw unfixed findings and scoped PostgreSQL exceptions remain visible in the evidence.

Provider and registry failures use local doubles. SARIF gates use structured controls; a complete CodeQL analysis and mutation campaign were not rerun. The restore drill uses genuine GPG and PostgreSQL in an isolated owned container with 17 synthetic rows and development TLS disabled; it did not restore live screening objects/escrow or measure production RPO/RTO. Clinical accuracy, live paid services, physical devices, OS storage eviction and production deployment were not validated. Browsers without Web Locks may retain additional immutable cache assets; v3 assets are intentionally retained for the upgrade. The original migration, recalibration, historical-screening review, metrics and trusted-backup-signer rollout requirements in [the remediation summary](audit-remediation-2026-10-04.md) still apply.
