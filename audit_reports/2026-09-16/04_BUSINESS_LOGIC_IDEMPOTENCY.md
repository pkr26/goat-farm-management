# 04 — Business Logic, Idempotency, State Machines (static + live)

Catalog items 4.1–4.5. Money integrity (4.1) and husbandry invariants (4.2) were
audited 2026-09-04/09-13 with remediations; this campaign re-falsified the
repairs and audited the remaining surface (4.3–4.5). Evidence:
`evidence/phase5_bizlogic.json`.

## Verdicts

| # | Area | Verdict |
|---|---|---|
| 4.1 | Money/finance integrity | DEFENDED (re-verified: no double-spend via idempotency replay — cached response returns same txn id; correction chain voids exactly once, `uq_transactions_active_source` DB backstop) |
| 4.2 | Husbandry invariants | DEFENDED (live: quarantine blocks sale 409; dead animals reject weight/patch; future breeding dates 422; negative/1e308 weights 422) |
| 4.3 | Idempotency abuse | DEFENDED |
| 4.4 | Simulation/planner input abuse | DEFENDED (pricing covers every cost-bearing field; compare sums costs; cancellation-safe leases) |
| 4.5 | State-machine smuggling | DEFENDED (live + static; DB CHECKs back the money paths) |
| 4.6 | Background loops | DEFENDED with 1 robustness note |

## Live highlights

- Idempotency: same key + same body → 201 replaying the cached transaction
  (same `id` — no double spend); same key + different body → 409 "already used
  with a different request"; missing key → 422 with explicit guidance;
  5,000-char key → 422.
- State machine: quarantined purchase animals cannot be sold/moved/bred (409
  quarantine protocol); DEAD animal rejects weight (400), PATCH (409); dead
  animal's insurance already auto-claimed by the mortality event (409) —
  intended linkage.
- Money bounds: negative/zero/quadrillion amounts → 422 (must be positive /
  ≤ limit); NaN → 422.
- Feeding: negative/1e12 inventory add, negative/huge dispense → 422;
  unknown recipe → 400.
- Pagination: offset 10000 → 200, 10001 → 422; limit 201/-1 → 422 (exact caps).

## Findings

| ID | Sev | Finding | Evidence |
|---|---|---|---|
| BIZ-1 | Low | Idempotent replay re-validates cached bodies against the *current* response schema — a post-deploy schema tightening turns ≤7-day-old replays into 500s (robustness, not attacker-triggered). | `app/services/idempotency.py:270,403` |
| BIZ-2 | Low | Cadence materialization loop has no per-farm error isolation: one persistently failing farm starves every higher-ID farm's cadence work forever (cursor resets each interval). No client-reachable trigger found. | `app/services/cadence.py:538-539`, `app/main.py:352-354` |
| BIZ-3 | Info | Insurance renewal to an arbitrarily distant future date books a single non-prorated premium row (register-only; no Transaction, no money impact). | `app/services/finance.py:252-266` |

## Held fixes re-verified

Cross-farm idempotency scoping (actor+farm+operation+key), password-material
purge from keyed fingerprints, `ON CONFLICT DO NOTHING` claim arbitration with
post-wait expiry re-check, overlapping-pregnancy unique constraint, self-breeding
DB CHECK, inbreeding two-generation fence, sale-from-quarantine block, simulation
`_run_cost` pricing (incl. ×11 ops-sim birth amplification), 50,000 head-days
cap, semaphore(2) fast-path 429 with keyed-lock release.
