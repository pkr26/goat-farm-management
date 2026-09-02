# Follow-up remediation — 2026-09-01 (post d0079d2)

An independent re-audit of commit d0079d2 (six parallel auditors plus
first-hand verification) confirmed every remediation was implemented, and
found 4 HIGH / ~14 MEDIUM residual defects. All of them are fixed here,
with regression tests in `backend/tests/test_audit_followups.py` and
`frontend/src/components/account-dialog.test.tsx`.

## HIGH

1. **Dairy weaning uncompletable for a VWP-re-bred dam.** The completion's
   pre-flight demanded a `weaning` RESTING transition from the dam even when
   the completion never moves her (a dam re-bred at her own 60-day VWP sits
   in BREEDING/PREGNANCY_* by day 90). The dam now joins the pre-flight only
   from the buckets the completion actually moves her out of
   (`services/tasks.py`). Regression test drives the full protocol: calve →
   fresh pen → AI at day 60 → weaning completes, heifer lands in FOUNDATION,
   dam stays in BREEDING.
2. **Seed corrections never reached existing databases** (ORF removal, PPR
   36→12 months, quarantine DM 0.8→1.1, legacy dairy delivery duties stuck
   at EDD−21). Migration `b5d7f9a1c3e5` backfills all four with guarded
   UPDATEs (never overwrites an operator-customized row), validated
   end-to-end against a pre-migration scratch database.
3. **Simulation mechanical errors.** Murrah operating costs now escalate at
   the matched 6% (was the 5% default — the "matched escalators" claim
   previously held only for feed); the planner no longer publishes negative
   `dry_does`; the engine caps litter size by species (buffalo ≤2, goat ≤4);
   calibration splits the wage bill on the engine's adult-female labour
   basis; the DSCR narrative states the principal-repaying denominator and
   dead code/contradictory comments are gone.
4. **Frontend rotation banner never cleared** after a successful password
   change. The refresh outcome's fresh user is now installed via a new
   `updateUser` on the auth context.

## Security / correctness (MEDIUM)

- The must-change-password fence now uses an explicit route allowlist:
  `POST /api/auth/farms`, `DELETE /api/auth/account` and
  `GET /api/auth/account/export` are fenced; bootstrap GETs
  (`me`/`permissions`/`farms`) and the rotation routes stay reachable.
- `milk_litres` floor is the storage floor (`ge=0.001`): a sub-milli value
  is a 422 input error instead of a flush-time CHECK violation (500).
- Finance-side milk floats are strict finite floats (no string coercion),
  matching the parlour contract.
- A legacy provenance-less MILK income row can be corrected again
  (amount-only); a replacement that *drops* the provenance of a row that
  carried it is refused.
- The sold-vs-produced milk guard is serialized by a per-farm advisory lock
  (namespace 4714, advisory-first ordering).
- The register per-email probe bucket is charged only for duplicate-email
  probes and resets on success — an IP-rotating attacker can no longer lock
  a fresh address out of registering, while enumeration of live account
  names stays throttled.

## Species hygiene (goat vs buffalo never mixed)

- Dashboard due-window now mirrors the write path exactly: goat EDD−15 pen
  move (day 135), buffalo EDD−60 dry-off (day 250) — not a generic 90% ratio.
- Creep feed requires the kid to be inside the species' weaning window
  (goat ≤60 days, age provable); a lactating yearling dam in RECOVERY is
  never billed on the calf creep line.
- The imported-in-milk exception requires a provable adult age (effective
  DOB present and ≥ the species' breeding age).
- Litter-cap violations raise a typed `LitterSizeError` → 422 (no substring
  matching on error text).

## Known residual (deliberate, documented)

- **Flagship viability is a product decision, not a patch.** After the
  mechanical corrections, both presets remain below the 1.20 DSCR floor
  (osmanabadi NPV ≈ −₹6.9L, murrah ≈ −₹9.7L; optimizer 0/109 feasible).
  The named root cause is unchanged: the repeat-breeder cull removes ~21%
  of breeding attempts with no purchase bridge in the engine. Making the
  flagships bankable requires real price/cost calibration by the product
  owner, not silent re-tuning — the DSCR gate surfacing infeasibility is
  the tool working as designed for a lender-facing projection.
- The goat simulation still disables `max_services_before_cull` (documented
  default) while the ops layer flags culls at 2 failed services; aligning
  it would silently re-tune flagship economics and belongs to the same
  product decision.
- The conftest auto-rotation hook is retained deliberately (documented test
  infrastructure with dedicated opt-out coverage of the fence).
