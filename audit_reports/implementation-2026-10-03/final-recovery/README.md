# Final local PostgreSQL recovery rehearsal

The [rehearsal](rehearse.py) exported a consistent repeatable-read PostgreSQL snapshot, used that exact snapshot for a custom-format `pg_dump`, and restored it into a uniquely named disposable database. The [receipt](receipt.log) records **49 tables, 2,458 synthetic rows**, final revision `f8e2f6a0c5d3`, matching counts and per-table canonical row SHA-256 digests, zero unvalidated constraints and Alembic/model parity. Local restore/reconciliation took **1.107 seconds**.

The owned restore database and dump were removed. No database contents or credentials are retained in this report. The source was the local synthetic E2E database; it was not a production backup. This demonstrates this local PostgreSQL restore path only. Object storage, configuration, encryption keys, production-sized timing, alert delivery and production RPO/RTO still require the complete staging drill in the improvement plan.
