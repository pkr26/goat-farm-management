# 02 — Multi-Tenancy & RBAC (static + live)

Catalog items 2.1–2.5. No Postgres RLS exists; all scoping is application-level — this is the crown-jewel audit. Static falsification across all 17 routers plus the **50-attack live cross-tenant matrix** (evidence: `evidence/phase2_crosstenant.json`).

## Live results (the headline)

Two farms (A=1 owner o1, B=2 owner o2), farm A seeded with animal/breeding/task/
purchase/insurance/health-event/scenario/plan/worker objects.

- **50/50 cross-tenant attacks → uniform 404, zero data leakage.** Owner o2's
  token with `X-Farm-Id: farmA` (not a member) was fired at every read and
  mutation endpoint for farm A's objects (animal GET/PATCH/move/sell/weight,
  breeding GET/abort/ultrasound, pregnancy, kidding-create-with-foreign-breeding,
  health restrictions/schedule/clear/event-create, lifetime-pnl, insurance
  claim/renew/list-filter, txn correct, purchase GET, task GET/complete/skip/
  reject/verify, scenario GET/PATCH/DELETE/run/compare, plan GET/PATCH/DELETE/
  dpr, team page + worker reset/role/status + role create, dashboard(+reports),
  buckets, feeding plan/inventory/dispense). No farm-A string ever appeared in
  any response body.
- **Own-farm foreign object IDs → 404** (cross-object probing within a tenant
  also fails).
- **X-Farm-Id header abuse**: duplicate headers → 400 (both orderings);
  `+1`, `-1`, huge, float, hex, empty, `0` → 400; non-ASCII → connection
  rejected; leading zeros (`01`) and whitespace-padded values parse to the same
  tenant and re-check membership (harmless canonicalization); duplicate
  Authorization headers → 401 (`single_bearer_token`).

## Static verdicts

| # | Area | Verdict | Key evidence |
|---|---|---|---|
| 2.1 | Farm-scoping exhaustiveness (17 routers, every endpoint) | DEFENDED | no id-fetch-then-authorize pattern anywhere; cross-farm buck/doe references blocked with exact-set lock checks (`api/breeding.py:249-260`, `api/kidding.py:242-254`); no schema accepts `farm_id` from bodies |
| 2.2 | X-Farm-Id parsing & membership | DEFENDED | `deps.py:521-586`: exactly-one header, `[0-9]+` fullmatch, `< 2**62`, int32 guard; membership resolved from DB per request (no farm claims in token); uniform 404 anti-enumeration |
| 2.3 | RBAC escalation (routes, delegation, presets) | DEFENDED | `tests/test_rbac_exhaustive.py` introspects the resolved dependency tree; worker-create is owner-only; `_guard_role_scope` enforces current+target ⊆ actor under FOR UPDATE; preset codes DB-CHECK-immutable; owner checks are per-farm `farm.owner_id == user.id` |
| 2.4 | TOCTOU / locking | DEFENDED | Membership→User→Role FOR SHARE fixed order on unsafe methods; `current_membership` refuses unlocked fallback (raises 401); deactivation fenced by per-request `is_active` DB check — no window |
| 2.5 | Quotas/ceilings | DEFENDED | distinct advisory namespaces (4711/4712/4713/4715); every count-then-insert inside the lock; 10-farms limit serialized via User FOR UPDATE; no bulk task-creation path |

## Findings

| ID | Sev | Finding | Evidence |
|---|---|---|---|
| TEN-1 | Info | `reports.view`-only callers receive ungated farm-wide `breeding.total_records`/`kiddings`/`total_kids_born` counts (rates are withheld; raw counts are not). Test-pinned as intended; weak volume inference only. | `api/dashboard.py:698-701,746`; `tests/test_dashboard_permissions.py:156-160` |
| TEN-2 | Info | Awaiting-verification tab is farm-wide for `tasks.verify` holders in `GET /api/tasks` while `GET /api/tasks/{id}` 404s the same rows (list-vs-single divergence, CLEANING category only). Intended; no endpoint-level parity test exists. | `api/tasks.py:279-283` vs `:399-405` |
| TEN-3 | Info (coverage) | No test pins X-Farm-Id leading-zero canonicalization; no endpoint-level list-vs-single task parity test. | — |

**Conclusion**: the application-level tenant barrier held under both code-level
falsification and live attack. The residual recommendation from prior campaigns
stands: consider Postgres RLS as defense-in-depth for the day a query author
forgets a predicate — today there are zero such rows.
