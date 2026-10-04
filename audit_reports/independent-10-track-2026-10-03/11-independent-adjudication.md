# Independent adjudication of tracks 01–10

Audit date: 3 October 2026 (America/Phoenix)  
Audited commit: `1ca78768ed227182b1b84663bcc97c5ea9bee41b`

## Scope and verdict

I first read the new campaign reports `01` through `08`, then independently extended the review to `09` and `10`. I checked every High and the consequential Medium findings against current source and focused, non-database evidence. I did not consult earlier audit reports, improvement/status files, mutation result artifacts, or the campaign's `98`/`99` files. No application file or database was changed.

The sole reported High, **SEC-01**, is confirmed at High **only with its stated prerequisites**. Considering Track 09's later runtime corroboration, of the 32 Mediums originally reported in Tracks 01–08 I retain 20 as Medium, downgrade 11 to Low, and treat one (`SEC-04`) as a real but explicitly documented product-risk decision whose defect status depends on the shared-tablet threat model. Tracks 09–10 add four unique Mediums after deduplication and severity challenge. I found no wholly invented High/Medium claim; the main adjudication issues are severity inflation, duplicate counting, and missing prerequisites.

## Confirmed High

### SEC-01 — rotating-source worker-PIN guessing: confirmed, conditional High

The target-only `(farm_id, membership_id)` bucket is deliberately soft: fresh-IP requests still reach Argon verification after it is full, failed guesses get `429`, and a correct PIN is accepted and clears the target bucket ([auth.py](../../backend/app/api/auth.py), worker-login block around lines 1193–1418). The checked-in test explicitly requires a correct PIN to succeed with that bucket full ([test_worker_pin_auth.py](../../backend/tests/test_worker_pin_auth.py), around lines 377–395). Production permits six-digit numeric PINs, while the unauthenticated roster is enabled by default and discloses membership IDs.

This is not unlimited compute: each source still meets the hard IP scopes and the two-slot, non-queuing Argon pool bounds concurrency. It is nevertheless uncapped *per target*, so the control does not stop a sufficiently distributed online search of a weak six-digit PIN.

High requires all of: worker PIN login exposed; a user-selected weak/six-digit PIN rather than the UI's random 12-digit suggestion; knowledge/discovery of farm and membership IDs; many source addresses; and enough time to consume the bounded Argon work. Without those conditions it is Medium. Fix the target risk without introducing permanent attacker-triggered lockout: after a small durable target budget, require provisioned-device proof, owner unlock, or another step-up challenge.

## Mediums confirmed at reported severity

| Finding | Adjudication |
|---|---|
| `01-M1` | Confirmed. The authenticated header is one non-wrapping control row, while account text appears at the same `md` breakpoint at which shell geometry changes. The reported 320/768 px clipping is consistent with the source and blocks farm/account/logout controls, not merely decoration. |
| `01-M2` | Confirmed. Route changes update the document title but never focus the keyed/focusable main landmark; SPA navigation can leave focus on the sidebar link. This is a material keyboard/screen-reader navigation defect. |
| `FE-02` | Confirmed. Loader epochs exclude `editorContentEpochRef`; herd import and calibration can replace edits made after their request began ([simulation/page.tsx](<../../frontend/src/app/(app)/simulation/page.tsx>), around lines 1683–1770). |
| `DB-01` | Confirmed. `MigrationSettings` ignores unprefixed variables and defaults to writable `localhost:5432/goatfarm`, and Alembic immediately uses that resolved URL. An independent no-I/O probe with only `DATABASE_URL`/`ALEMBIC_DATABASE_URL` set resolved to the localhost default. The track's accidental upgrade demonstrates practical, not hypothetical, impact. |
| `DB-03` | Confirmed. Screening rows retain object keys, while the supplied backup/restore path is PostgreSQL-only and there is no corresponding screening-object RPO/restore mechanism. This is a cross-system recovery gap whenever screening evidence is in recovery scope. |
| `05-1` | Confirmed. The service-worker navigation race returns any resolved `Response`; `response.ok` gates only cache publication. A fast `5xx` therefore beats a healthy cached shell ([sw.js](../../frontend/public/sw.js), around lines 112–127). Existing worker tests cover rejection/timeout fallback but not this non-OK response branch. |
| `05-2` | Confirmed at Medium after Track 09 runtime corroboration. The source-derived 31.5-second lie-fi path is now accompanied by a real-stack failure in which the controlled offline page remained on `Loading…` for the full 15-second assertion window despite a populated snapshot. Development-HMR noise means a production-mode reproduction is still desirable, but a critical promised offline workflow was demonstrably inaccessible, not merely delayed in theory. |
| `05-3` | Confirmed. A matching `review` receipt makes `persistWorkerOperation` return `OutboxReviewRequiredError`; UI cleanup removes only `sent` receipts. All non-`sent` records across actors/farms count toward the 100-record/2 MiB device cap ([worker-outbox.ts](../../frontend/src/lib/worker-outbox.ts), around lines 173–255). This can permanently block a corrected duty and eventually the whole shared tablet. |
| `SEC-02` | Confirmed, but lower priority than SEC-01. The email-only password bucket is also soft and a correct password remains accepted after the advertised target ceiling. Medium depends on distributed sources and plausible password reuse/dictionary guesses; the 12-character production floor and Argon admission materially reduce brute-force feasibility. |
| `SEC-05` | Confirmed. Security events are ordinary log calls, the supplied Compose stack retains only three 10 MiB local files, and some events occur before the domain transaction commits while others occur after. That is insufficient as the repository's durable forensic source, though an independently operated immutable collector could mitigate the retention half. |
| `PRIV-01` | Confirmed. Database screening retention is off by default, object deletion is wholly delegated to an external lifecycle, and raw retained uploads can still contain EXIF even though provider derivatives are scrubbed. Medium requires screening to be enabled and lifecycle/retention not separately provisioned. |
| `07-D1` | Confirmed. Active help promises unlimited AI breeding with zero bucks, while model version 3.3 removed AI capacity and the engine computes capacity only as `bucks × ratio`. This can materially invalidate financial/herd projections. |
| `07-D2` | Confirmed. Either one-sided reproduction calibration overwrites the entire `ParityMultipliers` object with two flat tables, altering the unobserved dimension. This is an unevidenced model change, not just a help-text defect. |
| `07-D3` | Confirmed. Whole-frame fallback runs only for zero boxes; any non-empty detector result screens crops only and aggregates only their statuses. A partial miss (or boxes truncated by the crop cap) can therefore yield image `HEALTHY` while a pictured goat was never screened. |
| `07-D4` | Confirmed. Successful gate/specialist/cross-check objects carry bounded parsed/raw evidence, but run details persist counts/flags rather than the conclusion-level observations. Findings preserve some final specialist/gate facts, but not enough to reconstruct replacements and disagreements. |
| `07-D5` | Confirmed. Healthy gate outcomes create no reviewable finding or negative dataset row; stats aggregate findings and flagged-run behavior. The UI promises “vet-confirmed precision” but renders counts, and README guidance recommends choosing an “accurate enough” provider from metrics that cannot measure misses. This needs healthy-outcome sampling and explicit denominators. |
| `07-D6` | Confirmed as an internal-consistency/workflow defect. Seed/migration policy is annual PPR, README says three-yearly, and farm cadence has no PPR round. A veterinary owner must decide which cadence is clinically authoritative before remediation; the adjudication does not endorse annual or triennial dosing. |
| `08-1` | Confirmed, workload-conditional. The endpoint performs five sequential counts and five row queries; each nonempty row query has three `selectinload` collections, giving the stated 25 task-domain statements. The worker can request 810 rows. Medium assumes populated tabs/history and nontrivial DB latency; the index-cost subclaim still needs production-shaped `EXPLAIN`. |
| `08-2` | Confirmed. Fairness is applied only after globally selecting the oldest 2,000 eligible images. The ~13 h scenario is conditional on 10,000 older legal in-flight rows, 50 claims/cycle, five-minute sleeps, and negligible processing time, but the starvation mechanism itself is exact. |
| `08-3` | Confirmed. One coroutine serializes outbox events, recipients, digests, and alert scans. After committing a claim, later reads open a transaction and the same session remains checked out during provider I/O. The ~18-minute example requires 50 recipients all hitting two 10-second connect timeouts plus backoff; it is a bounded scenario, not a measured production latency. |

## Confirmed facts, severity downgraded to Low

| Finding | Why Medium is not supported |
|---|---|
| `01-M3` | The English `StatusBadge` fallback in Telugu is real, but it is localized-content quality; it does not block the animal workflow. |
| `01-M4` | The worker surface lacks its own switch, but the device has a persisted language, defaults the worker surface to Telugu when unset, and can be provisioned in the intended language. Mixed-language handoff is a valid usability case, not demonstrated Medium functional loss. |
| `01-M5` | Some 409-only details bypass localization, but the safe/actionable detail remains visible and the operation is already rejected. Treat as localization hardening. |
| `FE-01` | The absent storage-key signal is real. After a successful logout, however, every protected API call rechecks the live refresh family and is rejected; the other tab mainly retains stale local UI until its next request/reload. A failed/offline logout is a separate inability to revoke the server session. |
| `FE-03` | Old-scope exports/toasts can continue after a switch, but the request preserves the farm from which the authorized user explicitly started it. This is confusing continuation ownership, not cross-tenant authorization or cache contamination. |
| `FE-04` | Cached duties mask a refetch error, but enabled offline actions are a deliberate product behavior: writes retain scope/idempotency and server conflicts become review receipts. The missing stale banner is important UX, but silent durable corruption was not established. |
| `BAPI-01` | The 23 non-standard `le` keys are independently reproduced and should be fixed, but runtime validation still rejects the values with 422. The present harm is inaccurate schema/client guidance, not acceptance of invalid money/weight facts. |
| `BAPI-02` | The test is undeniably self-comparing and its comment overclaims enum/breaking-change protection. Severity depends on an external backward-compatibility promise or independently deployed older clients, neither of which the report established. Keep as a CI/process gap. |
| `DB-02` | Omitted tables can grow monotonically, but the claimed 146,000 receipt rows per farm-year is not itself evidence of material storage/restore harm, and some rows are intentional audit/dedupe anchors. Define policy and measure growth before treating it as Medium. |
| `SEC-03` | Reset-password is indeed omitted from the memory-only credential routes. The stored value is a two-minute SHA-256 request-signature digest, not plaintext; exploitation needs same-origin/extension/profile access and a guessable password despite the production 12-character floor. Fix the clear policy inconsistency, but rate it Low. |
| `08-4` | Both catalogs are statically imported and the build measurement is useful, but “gzip-equivalent” size is not a field latency/parse measurement and no enforceable route budget was cited. This is a strong optimization candidate, not yet a Medium defect. |

## Disputed product-risk classification

### SEC-04 — 12-hour offline shift capability

The mechanism and abuse path are real: a same-tab `sessionStorage` marker unlocks an IndexedDB snapshot and permits local Complete/Skip drafts without a PIN; the original worker's later authenticated session drains them. However, the README and UI explicitly define this as the offline-shift contract, it grants no immediate API authority, and logout/End shift/expiry removes the capability.

Calling it a defect rather than an accepted risk requires the product threat model to say an unattended, still-open farm tablet must resist another person after the worker failed to end the shift. Preconditions are physical/same-profile access, the surviving tab marker, no End shift, and a later login by the original worker. Retain Medium if that threat is in scope; otherwise record a formal risk acceptance plus kiosk/inactivity guidance. Post-login review would materially reduce the integrity risk without caching a PIN verifier.

## Overlap and non-duplicates

- `SEC-01` and low-severity `PRIV-02` are a prerequisite chain, not duplicates. Closing unauthenticated roster discovery reduces target discovery; it does not cap guesses against already known IDs.
- `PRIV-01` and `DB-03` concern the same object store but opposite lifecycle guarantees: privacy deletion/finite retention versus backup/version recovery. Implement one version-aware object-governance plan, with distinct retention and recovery tests.
- `DB-02` overlaps that retention programme only operationally; it concerns relational growth/audit anchors, not screening-photo privacy or object recovery.
- `07-D3`, `07-D4`, and `07-D5` are distinct but should be one safety programme: coverage completeness, decision evidence, and negative-outcome evaluation. A whole-frame safety pass alone does not create reviewed healthy ground truth.
- `08-1` and low-severity `FE-05` are different layers of the same task-board cost: backend query/payload amplification versus redundant client query observers.
- `05-3` and `SEC-04` share the offline store but are not duplicates: the former is queue availability/recovery; the latter is physical-device authorization/integrity.
- `01-M3` and `01-M5` are separate manifestations of one localization policy gap: shared components/server details are allowed to bypass typed localized presentation.

## Independent verification

- Focused frontend tests: worker outbox, service-worker update harness, and idempotency persistence — **3 files / 71 tests passed**. The verbose cases confirmed permanent review receipts and also showed that the service-worker suite lacks a fast non-OK navigation case; the password-reset persistence exclusion is likewise untested.
- Pure backend simulation/financial/calibration-math tests — **267 passed**.
- Direct Pydantic schema probe reproduced `{"le": ...}` rather than `maximum` for weight and transaction amount.
- Direct, no-connection `MigrationSettings` probe with only unprefixed database variables resolved to `postgresql+asyncpg://localhost:5432/goatfarm`, `migration_database_url=None`, `environment=development`.
- Source tracing covered the security throttle order, live-session recheck, offline outbox/shift, screening cascade/stats, retention/backup boundaries, task query structure, and notification transaction/provider loops. No PostgreSQL, S3, external provider, or production service was contacted.

## Extension — Tracks 09–10

### Track 09 adjudication

| Finding | Adjudication |
|---|---|
| `09-1` | **Confirmed Medium.** The backend deliberately emits separate doe and buck `BREEDING` lines with the same bucket and recipe, but `PlanLineOut` carries no stable line/sex discriminator. Both React row identity and dispensing progress collapse to bucket/recipe (plus shift for progress). This creates a normal-state identity collision and can compare one aggregate dispense total independently against both sex-specific plans. Fix the domain identity/allocation semantics, not only the React key, and add a two-line breeding test plus a console-error gate. |
| `09-2` | **Exact duplicate of `BAPI-02`; no new finding.** It names the same same-checkout OpenAPI comparison and adds no independent failure mode. The cluster remains **Low** absent a demonstrated external compatibility promise or independently deployed older client. |
| `09-3` | **Downgraded to Low.** CI proves the mutation harness mechanics rather than a current application mutation score, exactly as both mutation READMEs disclose. A changed-file or scheduled mutation campaign would improve assurance, but no concrete escaped regression or promised threshold was shown. |
| `09-4` | **Downgraded to Low.** All automated screening tests stop at mocked storage/provider seams, so a scheduled sandbox contract is worthwhile. Screening is optional and hermetic adapter coverage is substantial; no actual provider/S3 incompatibility was reproduced. Medium would require a supported real integration failing or a stated deployment contract that this suite purports to validate. |
| `09-5` | **Downgraded to Low.** Aggregate thresholds permit a sufficiently small new production file at zero coverage, but the report expressly found no current wholly uncovered production file. This is a changed-line/per-file gate hardening opportunity, not a present Medium defect. |
| `09-6` | **Retain Low.** The worker/mobile journey is Chromium Pixel 7 only; a tablet breakpoint and mobile-WebKit smoke would close a real matrix gap, but no device-specific failure was demonstrated. |
| `09-7` | **Retain Low.** Import-time credential loading makes clean-checkout Playwright discovery fail before enumeration. Normal execution can still succeed after global setup, so the impact is reproducibility and tooling rather than product behavior. |
| `09-8` | **Retain Low.** The retry gate still fails flaky E2E correctly; the defect is lossy report retention and a JSON query that cannot name the flaky tests. |

Track 09's real-stack offline-worker failure is not a ninth finding. It materially changes the earlier adjudication of `05-2`: after the service worker was ready and the offline snapshot existed, the controlled page remained at `Loading…` for the entire 15-second user-action window. That directly exercises the auth-loading path behind the source-derived 31.5-second bound. I therefore restore `05-2` to **Medium**, while preserving the report's caveat that development-HMR failures warrant a production-mode reproduction.

### Track 10 adjudication

| Finding | Adjudication |
|---|---|
| `O10-01` | **Confirmed Medium.** Release requires successful ordinary CI for the tagged SHA but neither waits for nor calls the Security workflow. Release-local scans omit CodeQL, history-wide gitleaks, and the Compose infrastructure images. `O10-02` supplies a current case in which Security's image policy can fail while the release precondition remains satisfiable. |
| `O10-02` | **Confirmed Medium as patch/policy failure.** The exact nginx digest remains pinned and the Security workflow is configured to reject unsuppressed fixable High/Critical results. Track 10 reproduced two such Highs with a current database. Library-path reachability and remote exploitability were not established, so this is not a demonstrated compromise; re-pin and re-scan promptly. |
| `O10-03` | **Merge into `DB-03`; no new finding.** This is the same database-only recovery boundary, with important expanded scope. The single **Medium** recovery finding must now cover versioned/replicated screening objects **and** escrow/restoration of stable cryptographic material (especially the TOTP encryption key and idempotency HMAC), JWT material, DB CA, and backup GPG keys, followed by a post-restore decrypt/auth/object-integrity drill. Loss of these keys can make a sound database dump operationally incomplete even when object recovery is addressed. |
| `O10-04` | **Downgraded to Low.** The guard really does accept dual plain/`_FILE` delivery for optional credentials and the application silently prefers the file. Exploitation of the stale environment value still requires an operator to configure both routes and an actor with host/Docker or comparable process-environment access. Reject ambiguity for hygiene and reliable rotation, but Medium exposure was not shown. |
| `O10-05` | **Confirmed Medium, conditional.** Tag reruns can overwrite version tags/assets/digest notes, and the project has no cryptographic signing/consumer identity policy. Material substitution requires a tag move, rerun, or repository/package write compromise; external immutable-tag rules may mitigate the path but were not available for audit. Make published versions immutable and sign manifests/attestations/checksums. |
| `O10-06` | **Retain Low.** The repository supplies useful telemetry but delegates alerting, log export, and backup scheduling. External operational controls may exist. Its local-log subclaim overlaps `SEC-05`; count only the broader missing runnable operations layer here, not a second forensic-log defect. |
| `O10-07` | **Retain Low.** Missing license, disclosure, ownership, and contribution metadata affects distribution and incident coordination, not current runtime safety. |

### Duplicate and overlap resolution

- `09-2` and `BAPI-02` are one OpenAPI-baseline finding, counted once at Low.
- `O10-03` and `DB-03` are one recovery finding, counted once at Medium. `O10-03` broadens the remediation from database-plus-object recovery to include stable cryptographic-key custody and restore verification; that expansion must not be lost when closing the duplicate.
- `O10-06` partially overlaps `SEC-05` on local log retention, but its absent monitoring/alerting/backup scheduler is a distinct operational-control gap, so it remains one unique Low.
- Track 09's offline browser failure adds evidence to `05-2`; it does not add another finding.

### Final consolidated priorities

1. Close the conditional High `SEC-01` with durable per-membership step-up/device proof and an enforceable strong-PIN posture.
2. Fix `09-1` end to end: stable sex/segment-aware feeding-line identity, explicit dispense allocation semantics, and a regression that proves total planned feed cannot be falsely shown complete.
3. Make releases honor the exact-SHA Security result and immediately re-pin/re-scan the failing nginx edge (`O10-01`, `O10-02`).
4. Correct the material animal-care/model decisions (`07-D1`, `07-D2`, `07-D6`) and screening safety/evidence gaps (`07-D3`–`07-D5`).
5. Repair the field-offline failure set: non-OK shell fallback, fast offline auth release, and review-receipt recovery (`05-1`–`05-3`).
6. Make recovery cover the whole recoverable system (`DB-03`/`O10-03`): database, objects, stable application keys, JWT/CA/GPG material, and a timed post-restore drill. Pair this with explicit migration targeting (`DB-01`) and verifiable raw-photo lifecycle (`PRIV-01`).
7. Make published version artifacts immutable and cryptographically verifiable (`O10-05`), then address queue fairness/provider-session occupancy (`08-2`, `08-3`) and the remaining accessibility/scale work.

### Final consolidated unique severity counts

| Adjudicated class | Unique findings |
|---|---:|
| Critical | 0 |
| High | 1 |
| Medium | 24 |
| Low | 40 |
| Informational | 1 |
| Disputed product-risk classification (`SEC-04`) | 1 |
| **Total unique findings** | **67** |

The ten source reports contain 69 finding labels. The consolidated total removes exactly two duplicate labels (`09-2`/`BAPI-02` and `O10-03`/`DB-03`). It also reflects the evidence-driven reclassification of `05-2` from Low back to Medium; reclassification changes the distribution, not the unique total. `SEC-04` is kept outside the ordinary severity columns so the unresolved threat-model disagreement remains explicit rather than being silently forced into Medium or Low.
