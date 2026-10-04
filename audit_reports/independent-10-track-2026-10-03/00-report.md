# Independent ten-track audit — consolidated report

Audit date: 3 October 2026 (America/Phoenix)

Audited source commit: `1ca78768ed227182b1b84663bcc97c5ea9bee41b`

## Executive verdict

The project was audited through **10 separate lenses**, with one fresh reviewer per primary track and a later independent adjudication pass. The ten source reports contain 69 track-local finding labels. After removing two exact duplicates and challenging every High and consequential Medium, the consolidated result is:

| Adjudicated class | Unique findings |
| --- | ---: |
| Critical | 0 |
| High | 1 |
| Medium | 24 |
| Low | 40 |
| Informational | 1 |
| Disputed product-risk classification | 1 |
| **Total** | **67** |

The engineering baseline is strong: broad real-PostgreSQL and frontend test suites, current generated contracts, careful tenant/transaction boundaries, a durable offline outbox, digest-pinned deployment artifacts, and unusually defensive database backup/restore scripts. It is **not yet appropriate to treat the current checkout as production-certified**. The conditional High worker-PIN weakness, decision/screening correctness defects, a real feeding-line identity collision, field-offline failures, current edge-image vulnerability-policy failure, and incomplete whole-system recovery all need explicit disposition.

The final severity rationale, prerequisites, duplicate resolution, and source cross-checks are in the [independent adjudication](11-independent-adjudication.md).

## Important audit-side effect

During Track 4, a migration command intended for a disposable database used conventional unprefixed environment variables. The application accepts `GOATFARM_*` variables, ignored those values, and selected its writable development default. As a result, the existing local database `goatfarm` was unintentionally advanced from Alembic revision `f4a8c2e6d1b9` to current head `f9a3b7c1d5e2`.

No application source was changed and no downgrade was attempted. A read-only postcheck found one user, one farm, and one refresh session; all inspected screening, clinical-round, notification-log, and notification-outbox tables were empty; the migration inserted its two declared maintenance checkpoints. The single pre-existing refresh session has `session_origin IS NULL`, as the migration specifies. Because no pre-migration snapshot was captured, exact before/after value parity cannot be claimed.

The least-destructive action is to leave the database at head if it is meant to run this commit. If exact pre-audit state is required, restore a verified pre-audit backup into a separate empty database and validate it before switching connections. Do **not** blindly downgrade: several revisions intentionally refuse evidence-losing rollback or require session revocation. See the [full incident record and recovery guidance](98-audit-side-effect.md) and [Track 4](04-database-migrations.md).

## Independence method

- Each primary track was assigned to a separate reviewer.
- Primary reviewers were instructed not to read earlier repository audits, improvement/status plans, mutation campaign reports, or one another's conclusions.
- Findings required current `file:line` evidence, a concrete failure mode, confidence, and a distinction between runtime reproduction and inference.
- Reviewers made no application-code changes. The only Git-visible worktree addition is this new report directory.
- A separate reviewer then challenged severity, prerequisites, and overlap. Reclassification did not erase the original reports.

The exact protocol and deliberate limits are recorded in [00-method.md](00-method.md).

## Ten primary audit tracks

These are raw reviewer counts; use the adjudicated counts above for the consolidated risk picture.

| # | Independent lens | Raw result | Strongest signal |
| ---: | --- | --- | --- |
| 1 | [UI/UX, accessibility, localization](01-ui-ux-accessibility-localization.md) | 5 Medium, 6 Low | Authenticated header clipping and missing SPA focus handoff were reproduced. |
| 2 | [Frontend architecture and state](02-frontend-architecture-state.md) | 4 Medium, 1 Low | Late simulation loaders can overwrite edits made after a request begins. |
| 3 | [Backend and API contracts](03-backend-api-contracts.md) | 2 Medium, 3 Low | Runtime caps are emitted with non-standard OpenAPI keywords; the compatibility test has no prior baseline. |
| 4 | [Database and migrations](04-database-migrations.md) | 3 Medium, 3 Low | Alembic's implicit writable target was reproduced; recovery excludes referenced screening objects. |
| 5 | [Offline/PWA reliability](05-offline-pwa-reliability.md) | 3 Medium, 2 Low | Fast HTTP errors defeat a valid cached shell; review receipts can permanently block duties. |
| 6 | [Security and privacy](06-security-privacy.md) | 1 High, 5 Medium, 2 Low | Distributed sources can keep guessing a weak worker PIN without a hard per-target cap. |
| 7 | [Farm domain, AI, and decision accuracy](07-domain-ai-decision-accuracy.md) | 6 Medium, 2 Low | Simulation/calibration claims diverge from behavior; partial detector misses can still produce `HEALTHY`. |
| 8 | [Performance, scalability, and concurrency](08-performance-scalability-concurrency.md) | 4 Medium, 1 Low, 1 Info | Screening fairness begins after a global window; one serial notifier can stall tenants while holding DB capacity. |
| 9 | [Testing, QA, and evidence integrity](09-testing-qa-evidence.md) | 5 Medium, 3 Low | A real feeding identity collision emits React errors while E2E remains green; important seams remain unproven. |
| 10 | [Deployment, recovery, and operations](10-deployment-recovery-operations.md) | 5 Medium, 2 Low | Release does not require Security to pass; the pinned nginx digest currently violates its High-vulnerability policy. |

## What should be addressed first

1. **Close the worker-PIN target gap (`SEC-01`).** Apply a durable per-membership step-up after a small failure budget, retain source-level admission limits, enforce a strong generated-PIN posture, and reconsider default-open roster discovery. The High rating assumes a weak/six-digit PIN, discoverable target IDs, many source addresses, and time; without those prerequisites it is Medium.
2. **Fix feeding-line identity and allocation (`09-1`).** Doe and buck breeding lines can share the same bucket/recipe identity. Give each line a stable sex/segment-aware identity, define whether progress is line-specific or aggregated, and prove that one dispense total cannot independently satisfy both plans.
3. **Make release security binding and patch the edge (`O10-01`, `O10-02`).** Require the exact tagged SHA's Security workflow to pass. Re-pin and re-scan nginx: the audit's current exact-digest Trivy run found two unsuppressed and seven suppressed fixable High findings. Exploitability was not established, but the repository's own policy currently fails.
4. **Correct animal-care and model decisions (`07-D1`–`07-D6`).** Remove the nonexistent unlimited-AI behavior claim, stop one-sided calibration from flattening unrelated parity data, reconcile PPR cadence with a named veterinary authority, prevent incomplete detector coverage from becoming `HEALTHY`, retain bounded decision evidence, and sample healthy outcomes before claiming provider accuracy.
5. **Repair field-offline failure modes (`05-1`–`05-3`).** Prefer a valid cache over non-OK shell responses, release the offline page from auth bootstrap immediately and reliably, and provide a scoped retry/discard/resolution path for review receipts. The fresh browser suite reproduced the offline screen stuck at `Loading…`.
6. **Recover the whole system (`DB-01`, `DB-03`/`O10-03`, `PRIV-01`).** Require an explicit migration target. Coordinate PostgreSQL recovery with versioned screening objects and custody/restoration of TOTP/HMAC/JWT/CA/GPG material, then run a timed restore/decrypt/auth/object-integrity drill. Make raw-photo retention and deletion verifiable.
7. **Bound shared queues and finish access/scale work.** Move screening scheduling to farm-first rotation, separate and bound notification delivery without holding DB sessions during provider I/O, reshape the task-board query contract, fix header/focus accessibility, and make published artifacts immutable and cryptographically verifiable.

## Product decision still required

`SEC-04` is intentionally not forced into Medium or Low. A cached offline shift lets someone with physical access to the still-open tablet view the shift and queue Complete/Skip actions for up to 12 hours; it grants no immediate API authority and End shift/logout/expiry removes it. If the threat model requires resistance to a second person using an unattended tablet, treat this as Medium and add step-up/post-login review. Otherwise, document formal risk acceptance plus kiosk and inactivity guidance.

## Fresh verification

The coordinating pass independently ran:

- Backend Ruff: passed.
- Backend mypy: passed across 158 source files.
- Frontend typecheck, lint, and production build: passed.
- Runtime OpenAPI versus `shared/openapi.json`: exact equality, 108 paths and 242 schemas.
- Backend full suite on a uniquely named disposable PostgreSQL database: **5,166 passed, 4 skipped, 0 failed**.
- Frontend full Vitest suite: **5,281 passed, 0 failed** across 326 files.
- Real-stack Chromium Playwright, one worker and no retry: **73 passed, 1 failed**. The failure is meaningful runtime corroboration of `05-2`, not counted again.
- Frontend production dependency audit: zero reported vulnerabilities.
- Track 10 deployment-artifact suite: **131 passed**, with a valid synthetic production Compose render.

The disposable E2E database was dropped and ports 3000/8000 were confirmed closed. No real AI, SMS, email, payment, object-storage, or veterinary provider was invoked. Chromium was the only browser rerun by the coordinating pass; production-scale load, manual assistive technology, fluent Telugu review, real providers, and a full disaster restore remain external validation work. See [99-verification.md](99-verification.md) for the complete record.

## Bottom line

This is a mature, heavily tested project with several excellent controls, not a broken codebase. The independent review nevertheless found one credible conditional High and a concentrated set of Medium risks at trust boundaries: shared-worker authentication, animal-care/model correctness, offline field operation, cross-system recovery, and release security. Fixing those programmes should take precedence over adding features or chasing the long Low-severity tail.
