# Data Layer, Migrations & Contract Audit (2026-10-01)

Auditor 04 of 10 — DATA-LAYER INTEGRITY, MIGRATIONS & CONTRACT DRIFT.
Scope: `backend/app/models/*` (20 files), `backend/app/schemas/*` (21 files), `backend/alembic/env.py`, `backend/alembic.ini`, all migration files in `backend/alembic/versions/` (**95** `.py` files exist, not 96), `shared/openapi.json`, `frontend/orval.config.ts` + spot-check of `frontend/src/api/generated/`. All in-scope files read line-by-line; openapi.json verified by systematic enumeration (python) plus targeted Reads.

## Executive summary

**Verdict: PASS with minor observations.** This is one of the most disciplined migration chains and contract surfaces I have audited. The migration graph is structurally perfect (single head, one deliberate branch resolved by an explicit merge, every node reachable, every revision has a downgrade, only the merge downgrade is a no-op — correct). Destructive and type-changing migrations follow a consistent fail-closed preflight discipline that names offending row ids and refuses rather than silently rewriting. The float→numeric money/weight conversions, the tenant composite-FK program, and the CHECK-constraint vocabulary management are all executed to completion with model comments matching migration actions exactly at every one of ~40 points I spot-verified. The API contract is in exact bidirectional parity: 118 spec operations = 116 router operations + 2 app-level probes, zero drift in either direction; every enum in the spec matches its Pydantic Literal; all 55 strict input models carry `additionalProperties: false`; all requestBodies and path parameters are correctly marked required. The orval-generated client is fresh (version stamp 2.0.0 matches; newest fields/categories present in generated models).

No Critical or High findings. Two Low findings (lax-bool coercion in a small corner of the kidding/notification schemas; the documented orval-8 header-typing gap for `Idempotency-Key`), two Medium-conditional observations that depend on preconditions that cannot occur in the current codebase (historical same-revision rewrite caveat in `c8f1d3a5e709`; ORM-only `updated_at` bump semantics), and several Info-level notes.

Severity counts: **0 Critical / 0 High / 2 Medium / 2 Low / 6 Info** (plus positive observations).

## Findings

### [Medium] Historical same-revision rewrite caveat in the exact-money migration (conditional)
- Location: `backend/alembic/versions/c8f1d3a5e709_finance_feed_task_integrity.py:7-14`
- Evidence: the revision's own docstring records that "an earlier variant of this revision (same id) silently neutralized non-finite money values (``amount`` -> 0, optional prices -> NULL) instead of refusing … A database that migrated under that variant carries no marker of what was rewritten". The shipped preflight only protects databases that had not yet passed this revision.
- Impact: if any pre-release database migrated under the old variant, ledger/price values may have been zeroed/NULLed with no audit marker. The docstring states the rewrite "shipped before any external deployment", which confines this to internal/dev databases.
- Confirmed: the caveat text exists and the preflight cannot detect post-hoc which variant ran. Conditional: actual data loss only on pre-release databases, none of which are claimed to exist.
- Fix: none required now; if any pre-external-release database is still in use, compare its `transactions.amount = 0` / NULL-price rows against source records before trusting aggregates.

### [Medium] `updated_at` refresh is ORM-only; direct SQL updates do not bump it (conditional)
- Location: `backend/app/models/animals.py:295-300` (`Animal.updated_at`), `backend/app/models/core.py:141-146` (`Farm.updated_at`), `backend/app/models/notifications.py:90-97`, `backend/app/models/planner.py:65-72`, `backend/app/models/simulation.py:53-60`, `backend/app/models/screening.py:205-212`
- Evidence: `onupdate=utcnow` + `server_onupdate=text("timezone('UTC', now())")` are SQLAlchemy-ORM constructs; `server_onupdate` emits no DDL and no DB trigger exists (checked c8d2e6f0a4b3 and all later revisions). The columns were created in `c8d2e6f0a4b3` with server_default only.
- Impact: any out-of-band writer (psql, backfill script, future migration UPDATE) updates a row without refreshing `updated_at`. No consumer in the audited layers is currently wrong — `updated_at` is display/audit metadata, not a concurrency token (concurrency uses explicit `revision` columns on roles/scenarios/plans, which is correct design).
- Confirmed: ORM-only semantics; conditional on out-of-band writes.
- Fix: optional — a lightweight `BEFORE UPDATE` trigger on `animals`/`farms` if `updated_at` ever feeds staleness logic; otherwise document the ORM-only contract at the model.

### [Low] Lax boolean coercion in kidding and notification-preference inputs
- Location: `backend/app/schemas/kidding.py:35-37,72-73` (`KidIn.colostrum_within_2h`, `navel_dipped`, `dam_rejected`; `KiddingCreateIn.placenta_passed`, `mastitis_suspected`), `backend/app/schemas/team.py:68-74` (`NotificationPrefsIn` opt-ins)
- Evidence: these use plain `bool` while the codebase's stated contract is strict booleans — `schemas/team.py:34-38`: "StrictBool, like every other mutating boolean input: lax coercion would let `1`/`"false"`/`"off"` silently flip a worker's access". Pydantic v2 non-strict `bool` accepts `1/0`, `"true"/"false"`, `"1"/"0"`.
- Impact: a client sending `"dam_rejected": 1` or `"placenta_passed": "true"` is accepted here but 422-rejected on sibling endpoints — an inconsistency in the strict-input wire contract, not a data-integrity break (values are still genuine booleans after coercion).
- Confirmed.
- Fix: switch these fields to `StrictBool` (nullable variants `StrictBool | None`) to match the rest of the surface.

### [Low] Orval 8 does not type required header params on body routes (documented gap)
- Location: `frontend/src/api/custom-instance.ts:10-17` (documentation), `shared/openapi.json` (`Idempotency-Key` required on `POST /api/auth/farms`, `/api/feeding/dispense`, `/api/feeding/mix`, `/api/feeding/inventory/{item_id}/add`, `/api/finance/new`, `/api/purchases/new`)
- Evidence: the spec marks `Idempotency-Key` `required: true` on six POSTs; the generated functions carry no typed parameter for them. The mutator papers over it via `src/lib/idempotent-request.ts` which injects the header for exactly those routes.
- Impact: any consumer using the generated SDK **without** this custom instance gets 422s on six protected mutations. The spec itself is correct; this is a generator limitation, honestly documented, with a re-check note for orval upgrades.
- Confirmed (spec headers enumerated; generated signatures inspected).
- Fix: keep the documented workaround; add a CI assertion that the idempotent-request route list stays in sync with the spec's `required` header set (cheap drift tripwire).

### [Info] `NotificationRecipient` opt-in booleans have Python defaults but no server defaults (except one)
- Location: `backend/app/models/notifications.py:76-84`; migrations `e5a7c9d1b3f5` (columns NOT NULL, no default) and `a8c0e2f4b6d8` (`movement_restriction` with `server_default false`)
- Impact: a raw INSERT must supply five of the six opt-in booleans or fail NOT NULL. Model and migrations are mutually consistent (the asymmetry is historical), and the D7 timestamp-default waves deliberately targeted timestamps only. No drift.
- Fix: optional — backfill server defaults `false` in a housekeeping revision for direct-SQL symmetry.

### [Info] `KiddingRecordOut.kids: list[KidEntryOut] = []` uses a literal list default
- Location: `backend/app/schemas/kidding.py:110` (also `schemas/simulation.py` uses `Field(default_factory=…)` elsewhere)
- Impact: none — Pydantic v2 deep-copies defaults, so there is no mutable-default aliasing hazard. Purely stylistic inconsistency with the `default_factory=list` idiom used in sibling schemas.

### [Info] Wire accepts AI/AI_SEXED breeding methods the goat service refuses
- Location: `backend/app/schemas/breeding.py:18-22,31` — `BreedingMethodValue = Literal["NATURAL", "AI", "AI_SEXED"]` with an explicit comment that `services.breeding` rejects both for the goat-only product (pinned by tests).
- Impact: the contract advertises values that currently always 409 at the domain layer. This is deliberate future-proofing with documented tests; flagging only so contract consumers know.

### [Info] `BreedingCreateIn.heat_cycle_number` is accepted but never stored
- Location: `backend/app/schemas/breeding.py:33-39` — accepted "for wire compatibility", derived server-side from the doe's own history.
- Impact: none (documented; prevents client forgery of the first-cycle KPI). Contract consumers see a field the server ignores.

### [Info] Migration count discrepancy vs. audit brief
- The brief said "96 versions"; `backend/alembic/versions/` contains **95** `.py` files (plus `__pycache__`). All 95 were parsed for graph integrity and read in full. No gap results from the count difference.

### [Info] Positive-pattern: deliberate irreversibility is always documented
- Several downgrades intentionally do not reverse data repairs (`f4e5f6a7b8c9` purge, `a1b2c3d4e5f7` role repair, `e0f4a8b2c6d5` parity backfill, `b5d7f9a1c3e5` reference-data corrections, `a6c9e2f4b7d1` loss-attribution repair, `bd201c1cdc1b` dairy deletion), and two downgrades actively refuse when unsafe (`d4e5f6a7b8c9` with existing tombstones, `e6f8a0b2c4d7`/`d1e2f3a4b5c6`/`f9b3c7d1e5a2` CHECK-narrowing guards). This is the correct posture and each case carries an explanatory docstring.

## Coverage manifest

### backend/app/models (20/20 — all read line-by-line)
| File | Status |
|---|---|
| `__init__.py` | read — re-exports complete and consistent |
| `animals.py` | read — 30+ CHECKs, tenant composite FKs, exact numerics |
| `breeding.py` | read — outcome-state machine in CHECKs, partial unique open-pregnancy |
| `constants.py` | read — aliases of GOAT_PROFILE, sanity caps |
| `core.py` | read — users/farms/roles/memberships/refresh sessions/TOTP |
| `enums.py` | read — 24 vocabularies + `sql_in_values` single-sourcing |
| `feed_rules.py` | read — pure rules, no ORM |
| `feeding.py` | read — exact numeric(15,3) ledger |
| `finance.py` | read — ledger + insurance register/premiums |
| `health.py` | read — compliance CHECKs, schedule-template linkage |
| `helpers.py` | read — quarantine protocol, control-char guard |
| `idempotency.py` | read — actor/farm scoping with partial unique |
| `lifecycle.py` | read — pure transition graph |
| `notifications.py` | read — dedupe ledger, vocabularies |
| `planner.py` | read — jsonb shape guards, revision |
| `purchases.py` | read — provenance columns, bounded CHECKs |
| `screening.py` | read — 7 tables, bigint ids, tenant composites |
| `simulation.py` | read — jsonb assumptions |
| `species.py` | read — GOAT_PROFILE |
| `tasks.py` | read — lifecycle CHECKs, 5 partial indexes |

### backend/app/schemas (21/21 — all read line-by-line)
`__init__.py` (empty), `animals.py`, `auth.py`, `breeding.py`, `buckets.py`, `common.py` (strict bases, bounded money/quantity types, error contract), `dashboard.py`, `feeding.py`, `finance.py`, `health.py`, `kidding.py`, `ops.py`, `ops_simulation.py`, `owner.py`, `planner.py`, `purchases.py`, `screening.py`, `simulation.py`, `summaries.py`, `tasks.py`, `team.py` — all read; no `from_attributes` errors, no mutable-default hazards, no serialization aliases (snake_case throughout, matches spec), update models are explicitly partial with optimistic-concurrency tokens.

### Alembic environment
- `backend/alembic/env.py` — read (async engine, advisory lock vs. restore.sh, `lock_timeout=10s`, TLS parity, migration-specific statement timeout, deliberate no naming-convention with recorded rationale).
- `backend/alembic.ini` — read (no URL; supplied from settings; UTC timezone).

### backend/alembic/versions — 95/95 read line-by-line, in chain order
| # | File | Status |
|---|---|---|
| 1 | `ed5efe13a516_initial_schema.py` | read — baseline (Float money, superseded) |
| 2 | `c4b72167db86_simulation_scenarios.py` | read |
| 3 | `d8f2b6a41e90_concurrency_indexes.py` | read |
| 4 | `91a712b0367b_tasks_skipped_by_id.py` | read |
| 5 | `b3e91c47a2f5_refresh_sessions.py` | read |
| 6 | `e4a7c1f29b63_query_performance_indexes.py` | read |
| 7 | `f5b1a09c8d7e_schema_hardening.py` | read — FK ondelete policies |
| 8 | `a7c9e2f4b1d8_auth_session_and_team_hardening.py` | read — role FK CASCADE→RESTRICT |
| 9 | `a4d9e6f2b701_domain_integrity_traceability.py` | read |
| 10 | `c8f1d3a5e709_finance_feed_task_integrity.py` | read — float→numeric money w/ preflight (Medium-1 caveat) |
| 11 | `d9e4b7c2a610_movement_clearance_audit.py` | read |
| 12 | `e2c4f6a8b0d1_idempotency_records.py` | read |
| 13 | `f7d8c9b0a1e2_tenant_composite_foreign_keys.py` | read — cross-tenant preflight + NOT VALID/VALIDATE |
| 14 | `b9e1c2d3f4a5_reproductive_outcome_integrity.py` | read — triggers created |
| 15 | `c1d2e3f4a5b6_domain_check_constraints.py` | read — 80 CHECKs, preflighted |
| 16 | `d2e3f4a5b6c7_purchase_batch_sex.py` | read |
| 17 | `d3f4a5b6c7d8_health_restriction_audit.py` | read — audit table + immutability trigger |
| 18 | `d4e5f6a7b8c9_user_tombstones.py` | read — guarded downgrade |
| 19 | `e5f6a7b8c9d0_query_path_indexes.py` | read — CONCURRENTLY + invalid-remnant handling |
| 20 | `f6a7b8c9d0e1_refresh_session_bounds.py` | read — bounded compaction |
| 21 | `d7e8f9a0b1c2_health_schedule_template_links.py` | read |
| 22 | `d8e9f0a1b2c3_feeding_daily_indexes.py` | read |
| 23 | `e9f0a1b2c3d4_bounded_team_task_lifecycle.py` | read |
| 24 | `f0a1b2c3d4e5_pending_animal_task_cleanup_index.py` | read |
| 25 | `f1b2c3d4e5f6_active_membership_cleanup_index.py` | read |
| 26 | `f2c3d4e5f6a7_feed_quantity_numeric.py` | read — float→numeric(15,3) w/ preflight |
| 27 | `f3d4e5f6a7b8_actor_scoped_farm_idempotency.py` | read |
| 28 | `f4e5f6a7b8c9_purge_sensitive_idempotency_hashes.py` | read |
| 29 | `a1b2c3d4e5f7_repair_non_pending_personal_task_roles.py` | read |
| 30 | `b2c3d4e5f6a8_utc_transaction_created_at_default.py` | read — CURRENT_TIMESTAMP tz-corrected |
| 31 | `c4d5e6f7a8b9_tasks_rejection_attribution.py` | read |
| 32 | `d1c2b3a4e5f6_unassessed_breeding_service_outcome.py` | read |
| 33 | `e3f4a5b6c7d9_domain_audit_correctness.py` | read — tag-namespace triggers |
| 34 | `a6c9e2f4b7d1_ops_and_feed_provenance_corrections.py` | read |
| 35 | `b7c8d9e0f1a2_health_compliance_metadata.py` | read |
| 36 | `c2a4e6b8d013_animal_sire_index.py` | read |
| 37 | `d3b5f7c9e024_tag_namespace_farm_lock.py` | read — advisory-lock arena fix |
| 38 | `e6f8a0b2c4d7_monetized_cull_dispositions.py` | read — CHECK swap w/ rename |
| 39 | `b1c2d3e4f5a6_integrity_concurrency_hardening.py` | read |
| 40 | `c3d4e5f6a7b1_kid_entry_tag_index_predicate.py` | read |
| 41 | `d5e7f9a1b3c4_unique_preset_role_codes.py` | read |
| 42 | `e7f9a1b3c5d8_kidding_trigger_lock_order.py` | read — deadlock fix |
| 43 | `b3d7f1a5c9e2_farm_type_and_dairy_support.py` | read — dairy added |
| 44 | `c5a8e1f3b7d2_milk_correction_audit_and_fat_pricing.py` | read |
| 45 | `f8a2c4e6b1d9_widen_preset_role_codes.py` | read |
| 46 | `d1e2f3a4b5c6_widen_gestation_check_constraints.py` | read |
| 47 | `e3a5b7c9d1f2_worker_password_rotation_flag.py` | read |
| 48 | `b5d7f9a1c3e5_backfill_species_reference_data.py` | read |
| 49 | `f9b3c7d1e5a2_widen_kid_count_check_for_goat_quadruplets.py` | read |
| 50 | `a1b2c3d4e5f6_planner_plans.py` | read |
| 51 | `bd201c1cdc1b_goat_only_simplification.py` | read — dairy fully removed, child-first |
| 52 | `e8b0d2f4a6c1_tenant_history_pagination_indexes.py` | read |
| 53 | `b6d8f0a2c4e6_history_tables_tenant_foreign_keys.py` | read — history-table tenant FKs |
| 54 | `c4f6a8b0d2e5_json_columns_to_jsonb.py` | read — preflighted |
| 55 | `f1e2d3c4b5a6_husbandry_vocabulary_check_widenings.py` | read |
| 56 | `a7b8c9d0e1f2_kidding_husbandry_care_fields.py` | read |
| 57 | `b8c9d0e1f2a3_purchase_batch_provenance.py` | read |
| 58 | `c9d0e1f2a3b4_sale_capture_and_death_audit_fields.py` | read |
| 59 | `d0e1f2a3b4c5_insurance_register.py` | read |
| 60 | `e1f2a3b4c5d6_insurance_premium_history.py` | read — premium backfill |
| 61 | `f2a3b4c5d6e7_propagate_maintenance_recipe_fix.py` | read — guarded seed correction |
| 62 | `b7c1d5e9f3a2_task_provenance_title_keys_water_category.py` | read |
| 63 | `c8d2e6f0a4b3_updated_at_phenotype_vocabularies.py` | read — vocabulary normalization w/ refusal |
| 64 | `d9e3f7a1b5c4_exact_weight_numerics_index_hygiene.py` | read |
| 65 | `e0f4a8b2c6d5_backfill_kidding_parity.py` | read |
| 66 | `a19b2569d466_users_totp_columns.py` | read |
| 67 | `a7c8d9e0f1b2_add_disease_screening.py` | read |
| 68 | `b8d9e0f2a3c4_screening_phase2_rotation_specialists_review.py` | read |
| 69 | `c9e0f1a3b4d5_screening_phase3_crops_detection.py` | read |
| 70 | `d0f1a2b3c4d6_screening_disease_check_batches.py` | read |
| 71 | `c3e5a9f1d7b4_residual_numerics_next_due_jsonb_guards.py` | read — branch parent |
| 72 | `a6d4e2f9c8b7_insurance_audit_finance_planner_integrity.py` | read — branch A |
| 73 | `c4d8e1f9a2b7_screening_upload_hardening.py` | read — branch B |
| 74 | `b7e8f9a0c1d2_merge_screening_and_finance_integrity_heads.py` | read — merge (no-op upgrade/downgrade, correct) |
| 75 | `f7a9c1e3b5d7_screening_content_claims.py` | read |
| 76 | `b9c0d1e2f3a4_screening_attempt_budget.py` | read |
| 77 | `cad1e2f3a4b5_drop_unproducible_vocabularies.py` | read |
| 78 | `b1c3d5e7f9a2_drop_redundant_single_column_indexes.py` | read |
| 79 | `c3d5e7f9a1b3_totp_recovery_codes.py` | read |
| 80 | `d4e6f8a0b2c4_worker_pin_auth.py` | read |
| 81 | `e5a7c9d1b3f5_notifications.py` | read |
| 82 | `f6b8d0e2a4c6_data_housekeeping.py` | read — created_at wave + bigint PKs |
| 83 | `a8c0e2f4b6d8_movement_restriction_alert.py` | read |
| 84 | `b9d1f3a5c7e9_screening_images_bigint.py` | read |
| 85 | `c3d4e5f6a7b8_famacha_duty_category.py` | read |
| 86 | `d7e9f1a3b5c7_notifications_tenant_hardening.py` | read |
| 87 | `e2f3a4b5c6d8_screening_image_fk_bigint.py` | read |
| 88 | `f3a4b5c6d7e8_sale_weight_bounded.py` | read |
| 89 | `c6d7e8f9a0b1_screening_finding_crop_bigint.py` | read — reordered vs. index build, documented |
| 90 | `a4b5c6d7e8f9_screening_join_indexes.py` | read |
| 91 | `b5c6d7e8f9a0_timestamp_server_defaults.py` | read |
| 92 | `e8f9a0b1c2d3_screening_images_bucket_width.py` | read |
| 93 | `c7d8e9f0a1b2_index_hygiene_wave2.py` | read — 13 drops + subsumed CHECK |
| 94 | `d9e0f2a4b6c8_timestamp_server_defaults_wave2.py` | read |
| 95 | `c1d3e5f7a9b4_notification_recipients_membership_tenant_fk.py` | read — **HEAD** |

Graph verification (parsed every `revision`/`down_revision`): single head `c1d3e5f7a9b4`; one branch point (`c3e5a9f1d7b4` → `a6d4e2f9c8b7` + `c4d8e1f9a2b7`) resolved by merge `b7e8f9a0c1d2`; no dangling parents; all 95 nodes reachable from the head; every file has a `downgrade()`; only the merge's is `pass`.

### shared/openapi.json — systematic coverage
- OpenAPI 3.1.0, version 2.0.0, 101 paths / 118 operations / 235 component schemas.
- **Responses**: every operation has a `responses` block; every operation has ≥1 2xx; only `/healthz` + `/readyz` (probes) lack 4xx — appropriate. Every 401/403/404/409/429 on non-probe routes references `ErrorOut` (0 exceptions); 422 documented as the `ErrorOut | RequestValidationErrorOut` oneOf union on all mutating/listing routes.
- **Parameters**: 0 path params not marked required; 0 params without schema; all requestBodies `required: true`. Header params enumerated: `x-farm-id` (required on all farm-scoped routes) and `Idempotency-Key` (required on 6 routes, optional on 14).
- **Path parity vs backend**: diffed spec paths against all `@router.*` decorators + prefixes across 17 routers — spec-only: `GET /healthz`, `GET /readyz` (defined directly on the app in `main.py:853-1052`); router-only: none. 118 = 116 + 2, exact.
- **Enum parity**: every string enum in every component schema matched value-for-value against the Pydantic `Literal` declarations harvested from `backend/app/schemas/**` (29 module-level vocabularies); the only enums without a module-level alias are inline `Literal`s (`StatusChangeIn.new_status`, `UserOut.totp_state`, `HealthEventIn.scope`, `MovementRestrictionActionOut.action`, `ScheduleRowOut.status`, `PlanLineOut.basis`, `ScreeningFindingReviewIn.status`, `ScreeningUploadIn.content_type`, `HealthBulkTargetIn.scope`) or belong to the `app/simulation` package — no drift anywhere.
- **Strictness propagation**: all 55 input models carry `additionalProperties: false` (matches `StrictInputModel`), 0 exceptions.
- **Required-field spot checks** (10 models incl. `AnimalCreateIn`, `KiddingCreateIn`, `TaskCreateIn`, `ScreeningUploadIn`, `NotificationPrefsIn`): required lists match the Pydantic defaults exactly; no required-but-missing or missing-but-required fields.
- **Response-model spot checks** (8 operations across animals/dashboard/tasks/finance/screening/team/kidding): 200/201 schema refs match the routers' documented models.

### frontend/orval.config.ts + generated client
- `orval.config.ts` read: input `../shared/openapi.json`, react-query client, split mode, `clean: true` (removes stale model files), single mutator `custom-instance.ts` (no error-handling override — it delegates to `apiFetchEnvelope`, which maps non-2xx to `ApiError`).
- `src/api/generated/endpoints.ts` (19,461 lines) + 403 model files. Freshness: header stamp `OpenAPI spec version: 2.0.0` matches; **all 101 spec path shapes present** in the generated URLs (parameterized routes use template literals), 0 generated URLs absent from the spec.
- Endpoint spot-check across every domain (25 URLs): auth (register, worker-login, totp/challenge), animals (list, status, move), breeding (ultrasound, abort), kidding, health (events, restriction clear), tasks (complete, verify), feeding (dispense), finance (new, insurance renew), purchases/new, screening (uploads, finding review), team (worker notifications), simulation/run, planner plans, ops-sim/run, owner/benchmarks, dashboard/reports — all present.
- Model-field spot checks: `FAMACHA` (newest task category, migration of 2026-09-23) in `taskOutCategory.ts`/`quarantineScheduleTaskOutCategory.ts`; `movement_restriction` in `notificationPrefsIn/Out.ts`; `rejected_by_id`/`skipped_by_id`/`title_key`/`title_args` in `taskOut.ts`; `UNASSESSED` in `breedingRecordOutOutcome.ts`; `created_at` in `weightRecordOut.ts`; `recorded_on` in `insurancePremiumOut.ts`. The client is current with the newest migrations.

## Positive observations

1. **Migration graph integrity is airtight.** Single head, one intentional branch converged by an explicit merge revision (with a docstring explaining why), no gaps, no dangling parents, downgrades everywhere — and downgrades that would lie (irreversible data repairs) either refuse with actionable messages or are documented as deliberately non-reversing.
2. **Fail-closed preflight discipline for every type change / destructive op.** The float→numeric conversions (money `c8f1d3a5e709`, feed quantities `f2c3d4e5f6a7`, weights `d9e3f7a1b5c4`, residual numerics `c3e5a9f1d7b4`), CHECK installs (`c1d2e3f4a5b6` and successors), tenant-FK validation (`f7d8c9b0a1e2`, `b6d8f0a2c4e6`, `d7e9f1a3b5c7`, `c1d3e5f7a9b4`), and vocabulary narrowings (`cad1e2f3a4b5`, `e6f8a0b2c4d7`) all classify offending rows first, name up to 20-30 ids, and abort the transaction rather than silently rewriting or half-applying. The lazy-CASE trick to avoid `'Infinity'::numeric` errors on PG<14 shows real depth.
3. **Money and measurement integrity.** All currency is `Numeric(14,2)`/`Numeric(12,2)`; weights/quantities exact numerics with documented scale rationale; `asdecimal=False` keeps the ORM/API float contract while PostgreSQL stays authoritative; NaN/Infinity text guards on every numeric CHECK; business caps (₹1e9) mirrored between Pydantic bounds and DB CHECKs, with the parity pinned by `tests/test_domain_check_constraints.py` and `test_schema_parity.py`.
4. **Naive-UTC datetime convention is complete and self-consistent.** `utcnow()` emits naive UTC; `timezone('UTC', now())` server defaults produce UTC wall-clock independent of the session TimeZone (the one `CURRENT_TIMESTAMP` slip was found and fixed by `b2c3d4e5f6a8` with a precise root-cause note); two waves (`b5c6d7e8f9a0`, `d9e0f2a4b6c8`) finished the server-default coverage; business dates use farm-IANA-timezone resolution with the `bucket_moves.effective_date` CURRENT_DATE default deliberately removed.
5. **Multi-tenancy is enforced at the database, not just the app.** Composite `(farm_id, id)` candidate keys + `(farm_id, <ref>)` composite FKs across every tenant-scoped relationship — including the late-caught stragglers (`weight_records`/`bucket_moves` in `b6d8f0a2c4e6`, `notification_log` in `d7e9f1a3b5c7`, `notification_recipients.membership_id` in the head revision) — plus cross-table trigger guards (tag namespace with a farm-scoped advisory lock after the lock-arena fix, reproductive-integrity and provenance triggers) and task assignment FKs targeting memberships.
6. **Contract hygiene.** `extra="forbid"` on every input model propagated to the spec; strict scalar types (`FiniteFloat`, `StrictInt`, `StrictBool`) so `true` can never become id 1; bounded ids/money/quantities; a machine-readable error contract (`ErrorOut`/`RequestValidationErrorOut`, coded 409s) documented identically on all 116 API operations; the orval pipeline is fresh, cleaned (`clean: true`), and its one generator gap is documented with a compensating transport and a re-check note.
7. **Index lifecycle is managed like code.** Waves of redundant-index drops (`d9e3f7a1b5c4`, `b1c3d5e7f9a2`, `c7d8e9f0a1b2`) each document which composite subsumes which single, keep load-bearing singles with justification (e.g., `uq_membership_user_farm`), and the model's index map was updated in lockstep — verified at every drop I cross-checked. CONCURRENTLY builds are preceded by invalid-remnant cleanup, offline `--sql` generation fails closed on remnants, and the `c6d7e8f9a0b1`/`a4b5c6d7e8f9` reorder shows the deploy-window lock interaction was actually reasoned about.
