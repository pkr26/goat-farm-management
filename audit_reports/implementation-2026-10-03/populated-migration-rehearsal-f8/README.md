# Populated historical upgrade to the final schema

**55 checks passed** for the final four forward migrations from historical head `f4a8c2e6d1b9` to sole head `f8e2f6a0c5d3`. The two databases were disposable local PostgreSQL databases containing deliberately synthetic historical records. [The complete receipt](summary.json) names every assertion; [the execution log](rehearsal.log), [fixture preparation](prepare.py) and [rehearsal](rehearse.py) are retained.

The rehearsal covers valid historical facts, legacy sessions, screening provenance/review metadata, conservative daily provider budgets and durable maintenance checkpoint seeding. Every original fact column is preserved exactly by SHA-256 comparison. Ambiguous review or mismatched run/image/crop ancestry rejects the upgrade with an actionable preflight message; all schema changes and facts roll back. New invalid records fail the actual PostgreSQL constraints.

Legacy unscoped grants require reauthentication; legitimate fresh scoped login/refresh works. In-flight legacy screening receives a conservative local-day budget hold, without invented provider calls or medical history. Independent reservations survive a parent rollback and repeated attempt identity is idempotent. The two maintenance rows begin at cursor zero with no completed hour, claiming no historical processing. Both owned databases were dropped.

No paid provider calls or production data were used. This is a representative populated-schema rehearsal, not a production-size upgrade timing or staging deployment. Earlier 52-check evidence through `f7d1e5f9b4c2` remains separately preserved in `../populated-migration-rehearsal/`.
