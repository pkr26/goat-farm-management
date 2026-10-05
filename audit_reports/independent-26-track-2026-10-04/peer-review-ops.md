# Independent peer review of operations findings O26-01–O26-04 and O26-07–O26-08

Reviewed current commit `7245368fa91333fb39387df789fa4ef9a58dbea3` on 2026-10-04. This review challenged the new operations report against current source and its retained probes; it did not consult previous audit reports. No application source, external service, registry, or database was modified.

## Verdict

All four findings survive review within the report's stated prerequisites and limits. Retain High for O26-01 and Medium for O26-02/O26-03. O26-04 is a conditional recovery-manifest authentication defect: Medium is defensible for backup-integrity/RPO assurance, but it must retain its explicit metadata-write prerequisite and must not be presented as an unauthenticated application exploit or a broken database GPG signature.

| Finding | Independent checks and result | Prerequisites, severity and scope corrections |
|---|---|---|
| O26-01 production worker configuration failure | Read the actual production Compose worker environment, `metrics.enabled()`, `_record_run`, and worker cycle handling. Fresh isolated child processes accept `ScreeningWorkerSettings` and fail `_record_run` with the full API cookie validation error. Repeated with `GOATFARM_METRICS_ENABLED=false`: the same failure persists because the full settings object is constructed before the flag can be read. Reviewed the retained real PostgreSQL cycle test and five-attempt success/failure-provider results: they exercise the actual pipeline and durable state, not only a stub around `_record_run`. | **Confirm High.** Requires screening enabled and an eligible image reaching a provider call under the shipped role-separated production worker configuration. Images rejected/skipped before provider invocation are outside the claim; phrase “every image” as “every provider-processed image.” The cached test pool intentionally stays on its disposable database while only settings validation becomes production-shaped; this does not invalidate the configuration defect. Supplying API settings/secrets would mask the defect but is not the shipped topology or an appropriate telemetry requirement. |
| O26-02 process-local metrics not exported | Fresh worker-shaped process records a screening counter of 1; separate API-shaped process has no screening counter sample. Source confirms a private module-level `CollectorRegistry`, the API exports only that registry, worker starts no HTTP metrics listener, and supplied Prometheus scrape targets only the API/host exporters. | **Confirm Medium.** Current development topology is enough to reproduce; production metrics become relevant after O26-01 is repaired. This is missing observability, not loss of the durable budget ledger or a budget-enforcement bypass. Two-process isolation alone would not prove an operational defect if another exporter existed; source/topology review supplies that missing evidence. |
| O26-03 cleanup deletes pre-existing per-architecture package versions | Independently reran the retained exact-workflow-script probe with fake `gh`. An existing release makes preflight exit 1; no ownership marker exists; cleanup emits six DELETE requests for old per-architecture versions. Source confirms the failure guard is broad and those deterministic temporary names are unconditional; only the final release tag has the ownership guard. | **Confirm Medium.** Requires previous successful publish tags still present, operator rerun, and a token able to delete those package versions. No deletion is demonstrated when registry permissions refuse it. Do not escalate to proven final deployment-image loss: the test establishes emitted package-version deletion requests, not GHCR child-manifest garbage collection or actual final-index pull failure. Existing report already respects that limit. |
| O26-04 timestamp rewrite defeats freshness | Independently reran the synthetic-artifact probe: 72-hour inventory exits 2; changing only `generated_at` to now exits 0; archive hash and object recovery point stay unchanged. Read the producer's GPG command: only `backup.dump` is signed/encrypted; recovery JSON is bound/published afterward. Read inventory validation and freshness ranking: no signature authenticates metadata, while recovery age uses its declared capture time. | **Confirm conditional Medium.** Backup directory is created with restrictive permissions, and the monitoring systemd unit has read-only access to it. Therefore this is not an ordinary web user's write path, nor is accidental regeneration by the shipped capture/bind commands demonstrated: those commands refuse output overwrite. Threat is an actor/process able to replace backup sidecars or a separately introduced operator/storage rewrite. An actor controlling all backups could also destroy them; the distinct consequence here is false freshness despite intact authenticated database payload. Synthetic bytes are sufficient to isolate this verifier, but do not prove an end-to-end valid GPG restore or off-site compromise. |

## Fresh commands and evidence

From repository root:

```sh
backend/.venv/bin/python audit_reports/independent-26-track-2026-10-04/evidence/domain/peer_ops_probe.py > audit_reports/independent-26-track-2026-10-04/evidence/domain/peer_ops_results.json
```

Exit 0 in approximately 2 seconds. The independent harness creates isolated Python processes, explicitly sets both database URLs and `GOATFARM_TEST_DB` to a unique nonexistent audit database name before application imports, and performs no database/network I/O. It also reruns the reviewed operations backup and release probes, which use temporary local files and fake `gh` respectively. The full worker-cycle integration was source/evidence reviewed rather than redundantly rerun.

Evidence: [peer_ops_probe.py](evidence/domain/peer_ops_probe.py), [peer_ops_results.json](evidence/domain/peer_ops_results.json). Relevant source: `backend/app/metrics.py:27,89,138`, `backend/app/services/screening/pipeline.py:875`, `backend/app/worker/__init__.py:197–204,248–261`, `docker-compose.production.yml:307–354`, `backend/app/api/operational_metrics.py`, `ops/prometheus/prometheus.scrape.example.yml`, `.github/workflows/release.yml:118,508–538`, `backend/scripts/backup.sh:431–474`, `backend/scripts/recovery_inventory.py`, `backend/scripts/check_backup_freshness.py`, and `ops/systemd/goatfarm-backup-freshness.service`.

No additional independent issue ID is introduced by this peer review; these observations validate and bound O26-01–O26-04.

## Supplemental review: frontend container build inputs (O26-07 and O26-08)

**Both Medium findings are confirmed and distinct.** The unmodified Docker build currently stops at O26-07. O26-08 is a separately reproduced missing-input blocker reached after changing only the invalid package-manager declaration in a disposable diagnostic copy. The source repository remains unchanged. Neither receipt proves a completed frontend runtime image or successful container deployment.

### O26-07 — malformed Corepack package-manager declaration

Current `frontend/package.json:63` declares `pnpm@9.15.9+sha512-...` with a base64 integrity value containing additional `+` and `=` characters. This string is rejected by Corepack's package-manager specification parser before pnpm executes. The actual unmodified Docker receipt [frontend-container-build.txt](evidence/ops/frontend-container-build.txt) reaches `frontend/Dockerfile:6` and fails with `Invalid package manager specification ... expected a semver version`; this is a reached input-validation error, not speculation from Dockerfile inspection or a failed registry download.

An independent offline local control invokes the installed Corepack pnpm shim with `--version` in a temporary directory containing the exact current package/lock files. It exits **1** with the same message. Changing only the temporary declaration to `pnpm@9.15.9` makes the same command exit **0**, returning `9.15.9`. Network access is disabled for both controls. The local control establishes parser causality, while the operations Docker receipt establishes the pinned image's actual behavior.

**Scope/prerequisites:** A clean execution of the supplied Corepack-based dependency/build path. Existing local dependency installations or invoking a cached pnpm binary directly can bypass package-manager resolution, so a passing host lint/build/test run does not contradict this failure. The supplied CI frontend install also enables Corepack and invokes pnpm (`.github/workflows/ci.yml:270–282`); do not claim a historical remote CI failure without that remote run's receipt. This finding is a build/release blocker, not an application request-time vulnerability. Retain **Medium**, high confidence.

### O26-08 — required dependency patch absent from the deps stage

`frontend/Dockerfile:5` copies only `package.json` and `pnpm-lock.yaml` into `/app`; line 6 immediately installs dependencies. Both `frontend/package.json:77–78` and `frontend/pnpm-lock.yaml:19–22` require `patches/braces@3.0.3.patch`. That file exists in the repository and is not excluded by `frontend/.dockerignore`, but no instruction copies it into the deps stage. The broader `COPY . .` at Dockerfile line 17 occurs in the later builder stage, after the failing install, and cannot satisfy the missing earlier input.

Reviewed [probe_frontend_container_inputs.py](evidence/ops/probe_frontend_container_inputs.py), [its result](evidence/ops/frontend-container-input-results.json), [missing-patch receipt](evidence/ops/frontend-container-missing-patch.txt), and [positive-control install](evidence/ops/frontend-container-input-positive-control.txt). In the pinned Node container, only the package-manager declaration is temporarily changed; the exact deps-stage input set then exits **254** with `ENOENT` for `/app/patches/braces@3.0.3.patch`. Adding the repository's patch directory to that same disposable fixture makes `pnpm install --frozen-lockfile` exit **0**. This is a useful causal control; it is not a successful full application image build.

An independent short control invokes the cached exact pnpm 9.15.9 binary directly with `install --offline --ignore-scripts --frozen-lockfile` against only the two copied files. It reproduces the same missing patch and exit **254** without downloads or another container build. `--ignore-scripts` does not bypass patch-input hashing.

**Scope/prerequisites:** The dependency stage must get past O26-07, whether by correcting the declaration or explicitly selecting pnpm. No stale dependency cache is required to reproduce. O26-08 must remain described as latent behind the first blocker in the current unmodified build. The patch is not silently omitted from a running vulnerable image—the installation aborts—so retain **Medium** as a second build blocker, high confidence.

### Fresh peer evidence and limits

```sh
python3 audit_reports/independent-26-track-2026-10-04/evidence/domain/peer-container-inputs.py > audit_reports/independent-26-track-2026-10-04/evidence/domain/peer-container-inputs.json
```

Exit 0 in approximately 0.3 seconds, confirming all three asserted observations. [Independent harness](evidence/domain/peer-container-inputs.py) and [output](evidence/domain/peer-container-inputs.json) retain the exact commands, child exit statuses and error text. Package SHA-256 before and after is unchanged (`f01d81e68a55fa2c9cca2705fcb1c7fe95afef0ed9e8392eb508de27b66e6492`). No long build, external write, application source modification, or duplicate dependency installation was performed by this peer review.


## Coordinating reviewer: O26-09 artifact/table consistency

Independently parsed both policy JSON reports and compared every package/advisory/installed/fixed/severity tuple against the primary report: all **28 rows match exactly**, including duplicate advisories across different installed packages. Backend: 20 records, 16 distinct advisories, 17 HIGH/3 CRITICAL. PostgreSQL: 8 records, 4 distinct advisories, all HIGH. The preserved PostgreSQL ignore-file digest equals the unchanged current source file. This confirms evidence transcription and scoped policy consistency, not advisory reachability or a second independent scan. The Medium grouping and explicit runtime-exploitability limits are retained. [Machine-readable check](evidence/root/container-finding-review.json).
