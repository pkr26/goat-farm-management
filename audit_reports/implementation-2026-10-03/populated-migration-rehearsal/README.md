# Populated historical migration rehearsal

On 2026-10-03, the disposable rehearsal completed **52 assertion checks** successfully. This is a scripted migration/API/database rehearsal, not a pytest suite or production-data restore. It called no paid model, messaging, or storage provider and edited no repository application/migration source.

In the final strengthened run, two randomly named PostgreSQL databases were created at `f4a8c2e6d1b9`, populated independently, and dropped at completion. Each contained three farms with different timezones, three legacy refresh records (active, consumed, revoked), five screening images including an in-flight processing image, three crops, four run records (including a prior-day run and a historical error), four findings with pending/confirmed/rejected decisions and original reviewer metadata, and an existing sent notification fact. The fact-column comparisons included farms, roles, membership and recipient relationships.

## Upgrade preservation

The valid database upgraded directly through `f5b9d3e7a2c0` and `f6c0e4f8b3d1` to `f7d1e5f9b4c2`. Every original column value in the ten populated fact tables matched the pre-upgrade snapshot exactly. Historical review decisions retained revision zero and their actual reviewer IDs/timestamps/notes; no review history, round cohort, treatment coverage, or outbox alert was invented. The existing sent notification retained its fields and a null new outbox link.

## Atomic rejection and reconciliation

The second database exercised three independently constructed bad legacy states that the historical constraints permitted:

- A confirmed finding with a reviewer ID but no review timestamp. The full `f4→head` upgrade failed with the explicit message: `Clinical preflight: inconsistent screening review metadata. Export and reconcile original records; no reviewer is fabricated.` The revision stayed at f4, and all column definitions, constraints, indexes, triggers, functions, and original data values remained unchanged. Thus the preceding f5 scope-column additions rolled back too.
- A run whose crop belonged to another image in the same farm. The f5-to-head upgrade failed with `Clinical preflight: inconsistent run/image/crop/finding ancestry. Export and reconcile original provenance before retrying.` All schema/data and the f5 revision were unchanged.
- A finding whose crop differed from its run's crop. It failed with the same actionable ancestry message and the same atomicity checks.

Only the deliberately changed synthetic fields were restored from their original fixture values between cases. Once reconciled, the second database reached f7 and all original fact values matched again. Post-upgrade direct writes also rejected partial review metadata (SQLSTATE 23514), a cross-image crop link (23503), and a mismatched finding crop (23514).

## Authentication cutover

Historical refresh rows retained unknown/null scope; the migration did not infer password authentication from an old row. Against the actual new FastAPI authentication routes, validly signed legacy access and refresh JWTs without the scv=2 origin contract were refused with 401. Even a current-format signed password refresh token could not reinterpret the seeded null-origin historical row. A fresh password sign-in retained legitimate owner authority and successfully refreshed; its new rotation rows had explicit PASSWORD origin with null farm/member binding. No fixture token or reusable password is printed in the logs.

## Conservative screening budgets

The processing-image farm received a cutover hold of 2,147,483,647 reserved calls for its farm-local date and could not admit another attempt with cap 10. Completed/empty farms were not assigned invented migration-time usage rows. The completed-run farm's first reservation conservatively seeded two attempts for each of its two same-local-day historical logical runs, then added the new reservation: 2×2+1=5. The prior-day run was excluded. A real parent transaction first changed the image attempt count. Its rollback restored that image field to its original value while the independently committed five-unit budget charge survived; replaying the same attempt ID left usage at five. A farm with no historical calls charged its first real reservation, and cap one rejected the second without fabricating a reservation. Two successful durable reservations existed; rejected attempts added none. No outbound HTTP/provider call was made.

## Evidence and limits

`summary.json` records all 52 passing checks and canonical snapshot hashes. `rehearsal.log` contains the full assertion/API run; the individual upgrade logs retain Alembic results and the expected failure traces. `historical-schema.json` captures the old table shapes. Both owned databases were confirmed absent from `pg_database` at completion. Migration-file hashes before/after were identical.

This adds populated synthetic proof to the separate blank-schema roundtrip. It does not measure production table sizes/locking durations, validate a real backup's data distribution, or remove the need to stop old screening workers and coordinate the scv=2 authentication cutover. Operators must reconcile inconsistent historical facts with their original records; this rehearsal does not authorize inventing reviewers or changing real provenance.

The archived `prepare.py` and `rehearse.py` ran from the backend virtual environment. Their paths are local to this workspace, and preparation must be run before rehearsal; the `/tmp` manifest binds the random database names. Rehearsal owns and deletes only names matching its random `herdly_populated_*_test` manifest.

The initial 51-check run is retained under `first-run/`; the final run adds an actual pending image-write rollback assertion. Both runs completed successfully and removed their independently named databases. The root evidence files and `summary.json` refer to the final 52-check run.
