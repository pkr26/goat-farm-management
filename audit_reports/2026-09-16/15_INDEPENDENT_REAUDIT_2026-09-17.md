# 15 — Independent Re-audit of commit d341416 (2026-09-17)

An independent audit of everything shipped in commit d341416 (the
2026-09-16 remediation campaign + TOTP two-factor) re-reviewed all 94 changed
files: the TOTP crypto/auth surface line-by-line, the character/validation
modules executed directly (RFC 6238 vectors pass; ±1 drift, replay
high-water mark, AES-GCM roundtrip, Telugu ZWJ/ZWNJ preservation verified by
execution), frontend and infra/CI via parallel deep-dive sub-audits, and the
migration chain reconstructed programmatically. Reports 00–14 are preserved
unchanged as the historical record of the campaign; this addendum records
where that campaign's own remediation fell short, and the corrections that
landed on 2026-09-17.

## Findings and corrections

**HIGH — INFRA-3 restore floor compared random Alembic ids lexicographically.**
`[[ "${restored_revision}" < "${MIN_RESTORED_REVISION}" ]]` is string
comparison; migration ids are random hex. Of the 39 chain revisions at-or-
after the floor, 36 — including the then-head `a19b2569d466` — sort BELOW
`f4e5f6a7b8c9`, so every current-deployment backup was refused; meanwhile
`f5b1a09c8d7e`, `f6a7b8c9d0e1`, `f7d8c9b0a1e2` pre-date the purge yet sort
above the floor and were accepted (the exact fail-open the control existed
to prevent). The check also ran only AFTER the restore transaction had
committed, and the pinning test was tautological (string presence). The DR
drill's clean restore used `e0f4a8b2c6d5`, which the floor now refuses —
the floor was never behaviorally exercised.
*Correction:* `backend/scripts/restore_floor.sh` decides by chain
membership (ordered allowlist, floor→head, bash-3.2 compatible);
`restore.sh` extracts the archive's marker with
`pg_restore --table=alembic_version` and refuses PRE-FLIGHT, before any
connection to the target, and re-checks the restored database post-commit.
Behavioral tests run the helper across the chain boundary (including the
three fail-open ids), a chain-sync test regenerates the allowlist from
`alembic/versions` (a migration that forgets the list fails CI), and an
end-to-end mock-restore test proves a `f7d8c9b0a1e2` backup is refused
with no `psql` call in the log.

**MEDIUM — INFRA-5 README S3 guidance contradicted backup.sh.** A
PutObject-only credential fails the mandatory pre-upload collision check
(`s3:ListBucket`) before any upload, and the failure path runs `aws s3 rm`
on the run's own partial objects. The README now documents the three
actually-required verbs (PutObject; ListBucket prefix-conditioned;
DeleteObject for best-effort partial-publication cleanup) and the
retention-pruning caveat.

**LOW — corrected:**
- `/api/auth/totp/confirm` had no throttle (unthrottled 6-digit oracle for
  a stolen session against a PENDING secret). Now 5/5-min per account and
  per IP|account, mirroring the challenge path, reset on success.
- `totp_disable` re-fetched after the password confirmation without
  re-checking state (assert → 500 on a concurrent disable; a concurrently
  ACTIVE enrollment could be cleared on PENDING-strength checks).
  Restructured as a bounded dispatch loop that re-reads state under the
  final row lock; no assert remains.
- A wrong TOTP code incremented the "429 decision" counter (and the
  periodic summary) although the answer was a 401. The metric now counts
  only actual 429s; the summary semantics test pins it.
- `/api/auth/totp/challenge` was absent from both `REFRESH_COOKIE_POST_ROUTES`
  (Set-Cookie outside the cross-tab auth-cookie lock) and `NO_REFRESH_PATHS`
  (a mistyped code spun up the full refresh/teardown machinery). Added to
  both; tests assert no refresh call around a wrong code.
- The login response was hand-typed `TokenOut` with an ad-hoc cast; now
  typed through the generated `LoginOut` contract with fail-closed
  narrowing.
- The account-dialog TOTP handlers used only `mounted` fencing, so a late
  enroll response could resurrect a stale secret after dialog close/reopen;
  they now fence on `dialogEpoch` like every sibling action.
- The account-dialog TOTP section was English-only; all its strings moved
  to i18n keys in BOTH locales (en/te).
- Login credentials stayed visible (and the submit button said "Save")
  during the code step; the credential block is hidden during the
  challenge (values retained for Back) and the button reads "Verify code".
- docker-compose's edge-cap comment claimed the two body limits "can never
  silently drift apart"; they are independent knobs. Comment corrected and
  the artifact test now pins the DEFAULTS' byte equality (1m = 1048576).
- README GOV-2 claimed the one-process invariant is "enforced at boot";
  it is warned at boot and enforced by the Dockerfile's `--workers 1`.
  Wording corrected.
- release.yml left the four unverified `-publish-*` tags in GHCR when
  digest verification failed. A cleanup step (failure/cancel) now deletes
  those package versions, best-effort with a loud warning if the token
  cannot delete; the publish-phase comment documents the bounded
  two-phase-push exception explicitly.

## Verification (2026-09-17 corrections)

- Backend: `ruff` + `mypy` clean; restore/floor/edge-cap artifact tests
  (98, mock-based, no DB) pass locally; DB-backed suites (incl. 3 new TOTP
  tests: confirm throttle, 429-summary semantics, pre-purge end-to-end
  refusal via the mock suite) run in CI.
- Frontend: `tsc --noEmit` clean; full vitest suite run locally against a
  portable Node (see run log); login-TOTP tests now also pin the
  no-refresh-on-wrong-code and hidden-credentials behaviors.
- `release.yml`/`security.yml` parse; `restore.sh` + `restore_floor.sh`
  pass `bash -n`; the floor helper smoke-tested against head, floor, the
  three fail-open ids, and malformed inputs.
