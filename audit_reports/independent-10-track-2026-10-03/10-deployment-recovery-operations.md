# Track 10 — Deployment, recovery, and operations

Audit date: 3 October 2026 (America/Phoenix)

Audited source commit: `1ca78768ed227182b1b84663bcc97c5ea9bee41b`

## Verdict

The deployment artifacts are materially stronger than a typical small single-host stack. Images and GitHub Actions are digest/commit pinned, the production topology is least-privilege and resource bounded, production configuration fails closed on many dangerous values, release builds compare published image identities with the images that were scanned, and the PostgreSQL backup/restore scripts have unusually careful integrity, confidentiality, concurrency, and empty-target protections.

The principal residual risk is at the boundary between those strong repository controls and operations outside the repository. A version can be released without the independent Security workflow being green; the currently pinned public nginx edge fails that workflow's own current vulnerability policy; the stated recovery objectives cover the database but not all state required to recover the application; and the production Compose topology contains instrumentation but not a runnable monitoring, alerting, or backup-scheduling layer. Two narrower supply-chain/secret-delivery controls also remain incomplete.

Finding count: **0 Critical, 0 High, 5 Medium, 2 Low**.

## Findings

### O10-01 — A release can publish without a successful Security workflow

- **Severity:** Medium
- **Confidence:** High
- **Evidence status:** Source-proven control-flow gap. I did not trigger or publish a GitHub release. The repository's current edge-vulnerability state provides a concrete example in which Security would fail while the release precondition can still pass.
- **Current evidence:** `.github/workflows/security.yml:3-8`, `.github/workflows/security.yml:57-115`, `.github/workflows/security.yml:214-239`, `.github/workflows/release.yml:49-66`, `.github/workflows/release.yml:102-191`

The tag workflow verifies that the tagged commit is reachable from `main` and has a successful `ci.yml` push run. It does not query or depend on `security.yml`. The release-local gates scan the backend and frontend images, but do not run CodeQL, the history-wide gitleaks scan, or the production Compose nginx/PostgreSQL scans. Because `security.yml` is a separate push/PR/schedule workflow and the release starts on a `v*` tag, a tag can be processed while Security is still running or after Security has failed.

**Impact:** A version and its application images can be presented as a release even when the same source revision has failed the repository's high-severity static-analysis, secret, or infrastructure-image policy. In the current checkout, a current scan of the exact production nginx digest has two unsuppressed fixable HIGH findings, yet that state is not part of the release gate.

**Preconditions:** A tagged `main` commit has successful ordinary CI but a failed, cancelled, absent, or still-running Security run. An exploitable outcome additionally requires a real finding in one of the omitted gates.

**Recommendation:** Require a completed successful `security.yml` push run for the exact tagged SHA, or consolidate the mandatory security jobs into a reusable workflow called by both push CI and release. Keep the release-local per-architecture application-image scan, but also gate the exact infrastructure digests referenced by the tagged production manifest.

### O10-02 — The pinned production edge currently violates the repository's fixable HIGH/CRITICAL policy

- **Severity:** Medium
- **Confidence:** High for package exposure and policy failure; low for remote exploitability of these particular library paths
- **Evidence status:** Reproduced with a current advisory database against the exact pinned image. No exploit attempt was made.
- **Current evidence:** `docker-compose.production.yml:363-378`, `.github/workflows/security.yml:231-239`, `.trivyignore.compose-images:13-35`, `backend/scripts/check_trivy_ignore_freshness.py:79-98`

Trivy 0.74.0, using its current database on 3 October local time, reported two unsuppressed fixable HIGH vulnerabilities in `nginx:1.30-alpine@sha256:dc5069ad14f19660b141b21236140b91656bf89bbc3e2417c70ae650cd66104c`:

- `CVE-2026-93990`, `libexpat` 2.8.4-r0, fixed in 2.8.5-r0.
- `CVE-2026-103111`, `pcre2` 10.48-r0, fixed in 10.49-r0.

With `--show-suppressed`, the same scan also showed seven fixable HIGH `libuuid` findings suppressed by `.trivyignore.compose-images`: `CVE-2026-53612`, `CVE-2026-53613`, `CVE-2026-53614`, `CVE-2026-76642`, and `CVE-2026-78408` through `CVE-2026-78410`. The ignore freshness check still exits zero because its marker is 19 days old and the enforced limit is 30 days; marker freshness therefore does not mean the pinned image is currently within policy.

**Impact:** The only public application edge carries fixable HIGH-severity package debt, and the scheduled/push Security container job now fails before considering the seven acknowledged suppressions. This weakens patch posture and can obscure newer findings among deliberately accepted debt. I did not establish that the static nginx configuration exposes the crafted-regex or XML-parsing paths, so this is not presented as a demonstrated remote compromise.

**Preconditions:** Exploitation requires a vulnerable library path to be reachable. The operational/policy failure requires only running the repository's documented current scan.

**Recommendation:** Refresh and re-scan the nginx digest, update the production/local manifests and Security workflow mirror atomically, and remove obsolete ignore entries. Treat newly unsuppressed findings as an immediate repin trigger instead of relying only on the 30-day marker-age gate.

### O10-03 — The stated 24-hour RPO / four-hour recovery path omits required non-database state

- **Severity:** Medium
- **Confidence:** High about repository coverage; medium about production exposure because external storage, secret-manager, or host-backup controls may exist outside this checkout
- **Evidence status:** Source-proven gap; no destructive restore or production disaster drill was performed.
- **Current evidence:** `README.md:447-511`, `README.md:1031-1052`, `README.md:1141-1143`, `README.md:1193-1208`, `backend/scripts/backup.sh:381-395`, `backend/app/models/screening.py:89-99`, `backend/app/models/screening.py:173-190`, `backend/app/services/retention.py:14-21`, `backend/app/core/config.py:1496-1508`, `docker-compose.production.yml:24-44`, `docker-compose.production.yml:248-254`

The supplied backup job creates and validates a PostgreSQL custom dump; it does not capture other application state. Screening rows retain S3 bucket/key references while the bytes live in object storage, and the retention implementation expressly delegates those objects to a bucket lifecycle policy. Production also depends on host-mounted JWT material and stable secret files, including the TOTP encryption key required to decrypt MFA ciphertext. The runbook gives the backup GPG keys a separately tested recovery path, but does not define an equivalent recovery/escrow and restore procedure for application key material or a versioned/replicated recovery path for the screening-object corpus.

**Impact:** After loss of the deployment host/secret store or screening bucket, a valid database restore can yield dangling image records, lost review evidence, invalidated sessions, and MFA ciphertext that the recovered service cannot decrypt. TOTP can be reset account-by-account through the documented database break-glass path, so the failure is recoverable but may violate the four-hour target and sacrifices established factors. Loss of the current idempotency HMAC also breaks continuity for retained replay records.

**Preconditions:** The relevant host-mounted secrets or screening objects are lost and no independent external snapshot/replication/secret escrow exists. A database-only incident does not trigger this failure.

**Recommendation:** Define a recovery inventory and ownership map for PostgreSQL, screening-object prefixes, JWT/TOTP/HMAC material, the DB CA, and backup GPG keys. Use versioning/replication or another tested backup for required objects; escrow stable cryptographic material separately with least-privilege recovery access; add object/key restoration and post-restore verification to the quarterly drill; and state separate RPO/RTO values where the retention policy intentionally permits image loss.

### O10-04 — The Compose guard accepts dual plain/file delivery for optional production credentials

- **Severity:** Medium
- **Confidence:** High
- **Evidence status:** Reproduced locally. A disposable dotenv with valid single-route required DB/HMAC values plus both `GOATFARM_S3_SECRET_ACCESS_KEY` and `GOATFARM_S3_SECRET_ACCESS_KEY_FILE` made `compose_env_guard.py` exit 0.
- **Current evidence:** `backend/scripts/compose_env_guard.py:56-67`, `backend/scripts/compose_env_guard.py:92-115`, `docker-compose.production.yml:147-174`, `docker-compose.production.yml:186-204`, `docker-compose.production.yml:283-315`, `backend/app/core/config.py:1319-1402`, `.env.example:117-143`

The guard's exactly-one check covers the three database URLs and the idempotency HMAC, and its ambiguity-only extension covers TOTP. It does not cover other pairs that production Compose forwards simultaneously, including the metrics bearer token, S3 access/secret credentials, Anthropic/OpenAI keys, and MSG91 key. Application settings silently prefer the file value when both are present.

**Impact:** An operator migrating one of these credentials to file delivery can leave a stale, potentially still-valid plain credential in Docker container metadata and `/proc/<pid>/environ`, defeating the stated reason for file delivery and creating ambiguous rotation state even though the service boots normally.

**Preconditions:** An optional credential is set by both routes, usually during migration or rotation. Reading it from container metadata still requires host/Docker-level or otherwise privileged visibility.

**Recommendation:** Maintain one authoritative list of every supported plain/`_FILE` pair and reject dual delivery for all of them in the preflight (absence can remain optional where the feature is disabled). Forward every pair to `config-guard`, add table-driven tests, and consider rejecting ambiguity again in each settings projection as defense in depth.

### O10-05 — Versioned release artifacts are overwriteable and have no cryptographic authenticity policy

- **Severity:** Medium
- **Confidence:** High for workflow behavior; medium for abuse likelihood because repository tag/ruleset settings were not visible
- **Evidence status:** Source-proven workflow behavior; compromise/tag-move impact is inferred. No registry or release was mutated.
- **Current evidence:** `.github/workflows/release.yml:259-305`, `.github/workflows/release.yml:343-365`, `.github/workflows/release.yml:433-478`, `README.md:546-553`

The workflow attaches BuildKit provenance and SBOM attestations, but the project explicitly states that neither images nor releases are cryptographically signed. Re-running a tag republishes the version tag, `--clobber`s its SBOM assets, and rewrites its digest notes. The workflow accepts any tagged commit reachable from `main`; repository tag protection is described only as an additional external control.

**Impact:** Consumers have no project-defined signature/identity verification step for the first digest they trust, and a previously announced version can acquire replacement images, SBOM files, and digest notes. A GitHub repository/package compromise or authorized tag move can therefore replace the distribution channel's account of what a version contains without violating an artifact signature.

**Preconditions:** A workflow rerun, tag move, or repository/package write compromise; externally enforced immutable tags/rulesets can narrow the path but were not auditable here.

**Recommendation:** Make version tags/releases immutable after successful publication; sign OCI manifests/attestations and release checksums using a documented identity policy (for example, keyless Sigstore with issuer/repository/workflow constraints); verify that policy in the deployment runbook; and require an explicitly new version for changed bits or assets.

### O10-06 — The production topology stops at instrumentation and delegates all actionable monitoring and backup scheduling

- **Severity:** Low
- **Confidence:** High about repository artifacts; medium about actual operations because an external monitoring stack may exist
- **Evidence status:** Source-proven repository limitation; outage/non-detection impact is inferred.
- **Current evidence:** `docker-compose.production.yml:7-16`, `docker-compose.production.yml:46-60`, `README.md:346-362`, `README.md:379-397`, `README.md:1137-1143`, `backend/app/main.py:932-948`, `backend/app/metrics.py:1-19`

The application exposes liveness, database readiness, protected Prometheus metrics, maintenance counters, structured logs, and a worker heartbeat. The supported production Compose file, however, ships only rotating local `json-file` logs and application services. It includes no scraper, alert rules, log export, backup timer, missing-backup freshness check, or notification receiver. The runbook tells the operator to supply these controls (including alerting on non-zero backup exit and a missing daily artifact), but provides no executable reference unit/timer or monitoring configuration.

**Impact:** A deployment that follows only the runnable repository topology can have unhealthy services, stopped scraping, security-event logs, or a broken/missing nightly backup go unnoticed; three 10 MiB local log files per service can then rotate away the evidence. This is an integration/runbook gap, not proof that a real deployment lacks monitoring.

**Preconditions:** Operators deploy the provided Compose stack without adding the explicitly delegated external controls.

**Recommendation:** Provide a minimal supported reference: systemd timer/service units (or equivalent) for backup and backup-age checks, Prometheus scrape and alert examples for readiness/worker heartbeat/background failures, and a documented external log sink. Add an operator acceptance test that deliberately breaks each signal and records notification latency.

### O10-07 — Published artifacts lack basic license and security/governance metadata

- **Severity:** Low
- **Confidence:** High
- **Evidence status:** Reproduced with targeted `git ls-files` checks. `LICENSE*`, `COPYING*`, `NOTICE*`, `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `GOVERNANCE.md`, and root/`.github` `CODEOWNERS` are absent.
- **Current evidence:** `backend/pyproject.toml:5-32`, `frontend/package.json:1-5`, `.github/workflows/release.yml:407-478`

The backend package metadata declares no license, and the repository has no license/notice file. It also provides no supported vulnerability-reporting route or code-ownership/governance artifact even though the workflow publishes GHCR images, SBOM assets, and GitHub releases.

**Impact:** External users cannot safely infer permission to use, modify, or redistribute the project, and reporters/maintainers lack a repository-defined private disclosure and ownership path. This primarily affects adoption, compliance, and incident coordination rather than runtime correctness.

**Preconditions:** External distribution, contribution, vulnerability reporting, or organizational handoff.

**Recommendation:** Add an approved license and dependency-notice process, `SECURITY.md` with private reporting/support expectations, and appropriate ownership/contribution governance. Populate package metadata consistently with the chosen license.

## Strengths observed

- **Reproducible, reduced images:** Both runtime bases are digest-pinned; backend installation consumes the frozen lock through a hash-pinned bootstrap; frontend installation is frozen. The final images remove unused package managers and run as UID 10001 (`Dockerfile:21-75`, `frontend/Dockerfile:2-40`).
- **Container and network hardening:** Production services use `no-new-privileges`, dropped capabilities, read-only roots/tmpfs where appropriate, CPU/memory limits, bounded logs, separate service secret directories, an internal application network, and a loopback-only public edge (`docker-compose.production.yml:7-44`, `docker-compose.production.yml:128-140`, `docker-compose.production.yml:259-330`, `docker-compose.production.yml:363-413`).
- **Fail-closed production posture:** Production forces `verify-full` database TLS, external image digests, an explicit CA, exact host/origin settings, stable JWT/TOTP/HMAC configuration, a separate DDL role, and a pre-migration config guard. The local TLS-off database topology refuses production (`docker-compose.production.yml:46-126`, `docker-compose.yml:89-129`).
- **Safe lifecycle behavior:** The backend uses exec-form PID 1, a 30-second graceful shutdown with a 40-second Compose grace period, one explicit worker, liveness/readiness separation, and a dedicated worker heartbeat (`Dockerfile:79-86`, `docker-compose.production.yml:128-145`, `docker-compose.production.yml:259-278`).
- **Strong build/release mechanics:** Actions are commit-pinned; CI tests migrations, backend/frontend behavior, dependency advisories, production builds, and three-browser E2E. Release scans both architectures, publishes provenance/SBOM attestations, and compares each published config digest to the locally scanned image before assembling the release manifest (`.github/workflows/release.yml:92-191`, `.github/workflows/release.yml:193-365`).
- **High-quality database recovery tooling:** Backup uses private same-filesystem temporary storage, a password-free libpq command line, custom-format validation, authenticated encryption/signing, checksums, collision refusal, concurrency locks, off-host upload, and retention. Restore pins snapshots, authenticates signer and checksum, rejects unsafe revisions/non-empty targets, shares the migration advisory lock, restores in one transaction, and validates the resulting Alembic marker (`backend/scripts/backup.sh:336-489`, `backend/scripts/restore.sh:250-410`).
- **Useful observability primitives:** Metrics have bounded labels and include HTTP, rate-limit, idempotency, simulation, screening-spend, and maintenance signals; health endpoints and worker health are explicit. These are good inputs for the missing deployment-level monitoring layer (`backend/app/metrics.py:1-115`, `backend/app/main.py:932-948`).

## Verification performed

- Verified `HEAD` exactly matched `1ca78768ed227182b1b84663bcc97c5ea9bee41b`.
- Rendered `docker-compose.production.yml` with synthetic non-secret required interpolation values: `docker compose ... config --quiet` exited 0. Nothing was started.
- Ran `backend/.venv/bin/pytest -q backend/tests/test_deployment_artifacts.py`: **131 passed** in 20.43 seconds.
- Reproduced O10-04 with a disposable dotenv; `compose_env_guard.py` exited 0 for dual optional S3-secret delivery. The artifact was deleted afterward with `apply_patch`.
- Ran `backend/scripts/check_trivy_ignore_freshness.py`: it exited 0 and reported the marker was 19 days old.
- Scanned the exact production nginx digest with Trivy 0.74.0 and a freshly downloaded advisory DB: **2 unsuppressed HIGH, 0 Critical**, plus **7 suppressed HIGH** with `--show-suppressed`.
- Exercised the edge port comparison inside the exact pinned nginx/BusyBox image after ShellCheck flagged its non-POSIX lexical operator; the runtime accepted 65535 and rejected 65536, 70000, and 99999, so no functional finding was recorded.
- No deployment, image push, release publication, paid-provider request, external message, or existing-database mutation was performed.

## Limitations

- No production host, TLS terminator configuration, cloud firewall, DNS, external PostgreSQL service, object-storage policy, secret manager, monitoring stack, GitHub ruleset/tag protection, or on-call system was available. External controls may mitigate O10-01, O10-03, O10-05, and O10-06.
- I did not execute a live backup/restore drill, PostgreSQL failover, WAL/PITR recovery, host-loss exercise, key recovery, rolling deployment, rollback, or object-store restore. Source controls and unit/static tests cannot establish the stated four-hour RTO.
- Current vulnerability scan results are time-sensitive. Package presence and scanner severity were reproduced; reachability/exploitability of the reported nginx-image library CVEs was not.
- The review did not mutate application source. The only persistent change is this report.
