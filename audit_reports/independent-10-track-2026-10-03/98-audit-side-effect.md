# Audit-side effect: local development database migration

## Summary

During Track 04 on 3 October 2026, an Alembic validation command unintentionally targeted the existing local PostgreSQL database named `goatfarm`. It advanced that database from revision `f4a8c2e6d1b9` to the current head, `f9a3b7c1d5e2`.

Application source files were not changed. The database has **not** been downgraded, because the applied revisions add and backfill evidence tables and several downgrades deliberately refuse populated data. An automatic downgrade could therefore destroy or invalidate local data.

## Cause

The reviewer invoked the following from `backend/` (credential redacted):

```text
DATABASE_URL='postgresql+asyncpg://track4:***@127.0.0.1:55444/track4db' \
ALEMBIC_DATABASE_URL='postgresql+asyncpg://track4:***@127.0.0.1:55444/track4db' \
ENVIRONMENT=development \
.venv/bin/alembic -c alembic.ini upgrade head
```

The application accepts `GOATFARM_*` settings. The unprefixed variables above were ignored. `backend/.env` was absent, so migration configuration selected its development default, `postgresql+asyncpg://localhost:5432/goatfarm`.

This violated the campaign instruction not to upgrade a non-disposable database.

## Confirmed state

A subsequent read-only `alembic current` reported:

```text
f9a3b7c1d5e2 (head)
```

The five applied revisions were:

1. `f5b9d3e7a2c0` — PIN refresh-session farm/membership scope columns and constraints.
2. `f6c0e4f8b3d1` — screening-review provenance, durable screening budgets, treatment-round evidence tables, guards, and a conservative budget backfill for processing images.
3. `f7d1e5f9b4c2` — transactional notification outbox and delivery linkage.
4. `f8e2f6a0c5d3` — durable maintenance progress table with two initial checkpoint rows.
5. `f9a3b7c1d5e2` — revision-zero history rows for qualifying legacy screening decisions.

The migration sequence completed successfully, so its transactional preflights and constraints did not reject the existing data. No before-migration row snapshot was captured by this audit, so the report does not claim exact before/after row-count parity.

## Recovery guidance

- If the local database was expected to be upgraded, leave it at head; this is the schema required by the current application commit.
- If the exact pre-audit database state is required, restore a pre-audit backup or snapshot into a separate database and verify it before replacing anything.
- Do **not** blindly run `alembic downgrade f4a8c2e6d1b9`: the revisions explicitly guard against losing new screening review history, clinical evidence, provider-budget records, or notification outbox records.
- Preserve the current database until the user decides whether the upgrade is acceptable or a known backup should be restored.

No restore, downgrade, or deletion was attempted after discovery.
