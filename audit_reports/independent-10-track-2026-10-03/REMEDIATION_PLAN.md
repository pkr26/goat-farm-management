# Full remediation ledger

Started: 4 October 2026 (America/Phoenix)  
Baseline commit: `1ca78768ed227182b1b84663bcc97c5ea9bee41b`

This ledger tracks the 69 primary labels and the 67 deduplicated findings from the independent audit. A finding is marked complete only after implementation, focused regression coverage, and an independent review pass. Exact duplicates remain listed but are closed through their canonical finding.

## Wave 1 — security, domain correctness, database, and operations

- [ ] `SEC-01` hard per-target worker-PIN protection
- [ ] `SEC-02` hard per-account password protection
- [ ] `SEC-03` no persistent reset-password verifier
- [ ] `SEC-04` harden unattended offline-shift capability
- [ ] `SEC-05` durable transactional security-event evidence
- [ ] `SEC-06` production cannot disable authentication limiting
- [ ] `PRIV-01` enforceable screening-photo lifecycle and metadata handling
- [ ] `PRIV-02` authenticated or explicitly provisioned worker-roster discovery
- [ ] `07-D1` align AI-breeding guidance and engine behavior
- [ ] `07-D2` preserve the uncalibrated parity dimension
- [ ] `07-D3` prevent partial detector coverage from becoming `HEALTHY`
- [ ] `07-D4` preserve bounded structured AI-stage evidence
- [ ] `07-D5` label conditional metrics and sample healthy outcomes
- [ ] `07-D6` unify PPR policy, authority, documentation, and tasks
- [ ] `07-D7` align breeding-age guidance with the canonical rule
- [ ] `07-D8` render withheld mortality values explicitly
- [ ] `09-1` stable feeding-line identity and dispense semantics
- [ ] `DB-01` require an explicit migration database target
- [ ] `DB-02` lifecycle for high-churn durable ledgers
- [ ] `DB-03` whole-system recovery for database, objects, and key material
- [ ] `DB-04` constrain contradictory screening evidence states
- [ ] `DB-05` tenant-bind audit actor attribution
- [ ] `DB-06` bound/rehearse the exclusive-lock migration
- [ ] `O10-01` bind releases to exact-SHA Security success
- [ ] `O10-02` re-pin the vulnerable production edge image
- [ ] `O10-03` duplicate of `DB-03`; retain expanded key-recovery scope
- [ ] `O10-04` reject every dual plain/`_FILE` credential route
- [ ] `O10-05` immutable and verifiable release artifacts
- [ ] `O10-06` runnable monitoring, alerting, logs, and backup scheduling reference
- [ ] `O10-07` license, security-reporting, ownership, and contribution governance

## Wave 2 — UI, frontend state, offline behavior, API contracts, and scale

- [ ] `01-M1` responsive authenticated header
- [ ] `01-M2` SPA route focus handoff
- [ ] `01-M3` localized status rendering
- [ ] `01-M4` worker-surface language control
- [ ] `01-M5` localized conflict presentation
- [ ] `01-L1` worker 44 px touch targets
- [ ] `01-L2` accessible overdue state
- [ ] `01-L3` semantic data-card headings/captions
- [ ] `01-L4` field-associated animal-create errors
- [ ] `01-L5` remaining locale/timezone bypasses
- [ ] `01-L6` broaden the automated accessibility gate
- [ ] `FE-01` reliable cross-tab logout signal
- [ ] `FE-02` protect simulation edits from late loaders
- [ ] `FE-03` cancel stale-farm screening continuations
- [ ] `FE-04` expose stale worker-board refresh failures
- [ ] `FE-05` collapse per-duty permission observers
- [ ] `BAPI-01` standards-compliant numeric OpenAPI maxima
- [ ] `BAPI-02` immutable compatibility baseline
- [ ] `BAPI-03` Bearer challenge on authentication failures
- [ ] `BAPI-04` reject malformed finance month filters
- [ ] `BAPI-05` accurate per-route response declarations
- [ ] `05-1` cache fallback for non-OK shell navigation
- [ ] `05-2` immediate reliable offline auth release
- [ ] `05-3` scoped review-receipt resolution and capacity
- [ ] `05-4` retire orphaned PWA assets
- [ ] `05-5` quarantine malformed legacy queue records
- [ ] `08-1` selected-tab task rows and bounded query work
- [ ] `08-2` farm-first screening fairness
- [ ] `08-3` bounded notification concurrency without DB-held I/O
- [ ] `08-4` split language catalogs and enforce route budgets
- [ ] `08-5` hash TOTP recovery codes outside locked DB work
- [ ] `08-6` enforce or externalize the single-replica boundary

## Wave 3 — QA evidence and final independent verification

- [ ] `09-2` duplicate of `BAPI-02`
- [ ] `09-3` changed-file/scheduled application mutation evidence
- [ ] `09-4` optional real storage/provider screening contract test
- [ ] `09-5` changed-line/per-file coverage floor
- [ ] `09-6` tablet geometry and mobile-WebKit worker smoke
- [ ] `09-7` side-effect-free Playwright discovery
- [ ] `09-8` durable successful-run E2E evidence and correct flaky-title query
- [ ] Full backend, frontend, build, contract, migration, and browser verification
- [ ] Independent diff review for each implementation workstream
- [ ] Final source/worktree, disposable-database, and service cleanup check

## Completion rule

“Fixed” means the product behavior or control is changed, a regression test or executable validation covers the failure mode, relevant documentation is updated, and a reviewer other than the implementer has checked the diff. Where external credentials, veterinary authority, repository settings, or legal ownership are required, the repository must fail closed or make the unresolved operator decision explicit rather than claiming unverifiable completion.
