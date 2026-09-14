# Red Team Audit — Part B: Tenancy, RBAC Core, Team Administration (Units B1–B12)

- **Date:** 2026-09-13
- **Target:** Herdly goat-farm SaaS monorepo, backend (`/Users/pavankumarreddyreddem/Desktop/goat_saas/backend/app`)
- **Scope units:** B1 Farm scoping, B2 Farm creation, B3 Farm listing/switching, B4 Permission resolution, B5 Dependency-chain locks, B6 Permission catalog/presets, B7 Worker provisioning, B8 Worker role reassignment, B9 Worker status changes, B10 Owner worker-password reset, B11 Custom role CRUD, B12 Team page aggregate.
- **Primary files read end-to-end:** `deps.py`, `api/team.py`, `api/auth.py`, `models/core.py`, `models/idempotency.py`, `permissions.py`, `seed.py`, `services/idempotency.py`, `schemas/team.py`, `schemas/common.py`, `schemas/auth.py` (EmailMixin/FarmCreateIn), `security.py` (password policy), `db.py` (session config). Test intent cross-checked: `test_team_extended.py`, `test_team_bugs.py`, `test_rbac_exhaustive.py`, `test_lock_order_hardening.py`, `test_auth_extended.py`, `test_adversarial.py`, `test_idempotency.py`.

## Methodology

Every enforcement point was read in code and attacked as written — never trusting docstrings, comments, or test names. For each unit: (1) reconstruct the exact authorization path (dependency graph, SQL emitted, lock modes); (2) attempt bypass variants (duplicate/ambiguous header inputs, cross-tenant ids, TOCTOU races on unlocked preflights, lock-order inversions, idempotency replay abuse, delegated-privilege ceiling escapes, enumeration oracles); (3) confirm each control is actually reachable on every route (verified `TEAM_PERM` wiring and FastAPI dependency resolution, including that a singular `Header()` param resolves via `Headers.get()` = first occurrence while the explicit `getlist() != 1` check in `current_farm` rejects ambiguity first, and that `int()` parsing quirks were empirically tested). Findings below are code-evidenced with file:line. Items marked **Held** were attacked and survived.

## Findings table

| ID | Severity | Unit | Title |
|----|----------|------|-------|
| RT-B-1 | Medium | B7 | `POST /api/team/workers` is a platform-wide registered-account existence oracle (201 vs 400) for any farm owner |
| RT-B-2 | Low | B9 | Owner-side worker status/reactivation path never revalidates target role liveness (skips `_pin_membership_role`) — defense-in-depth gap, currently unreachable |
| RT-B-3 | Low | B9/B10/B11 | No durable audit trail for team-administration mutations (password resets, role edits, deactivations, provisioning) |
| RT-B-4 | Low | B1 | `X-Farm-Id` integer parsing accepts non-canonical spellings (Unicode digits, `+N`, whitespace, `5_0`) — no authz impact, canonicalization nit |
| RT-B-5 | Info | B4 | Lock-free read-path authorization window (documented design; fail-closed after commit) |
| RT-B-6 | Info | B5 | `active_membership(lock_authorization=True)` is dead code and diverges from the canonical pin chain (no User re-pin) |
| RT-B-7 | Info | B12 | Team roster exposes workers' global emails and user ids to any `team.manage` holder (inherent multi-tenant tradeoff; noted per scope) |
| RT-B-8 | Info | B12 | Team-page response-overflow 409 can lock an owner out of roster-based management if the cap is lowered below live membership count |

No Critical or High findings. All candidate privilege-escalation, cross-tenant, and lock-inversion hypotheses were attacked and held (per-unit evidence below).

---

## RT-B-1 — Medium — B7 — Worker-create is a registered-account existence oracle

**Evidence:**
- `backend/app/api/team.py:900-905` — inside `mutate()`: `worker = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none(); if worker is not None: raise HTTPException(status_code=400, detail=CANT_ADD_TO_TEAM)`.
- `backend/app/api/team.py:907-919` — a non-existing email proceeds to `User(...)` insert and returns `201` with a full `MembershipOut`.
- `backend/app/api/team.py:63` — `CANT_ADD_TO_TEAM = "That email can't be added to this farm's team."` — deliberately identical for owner/worker/unaffiliated accounts (anti-*categorization*), but the status code still partitions existence.

**Exploit sketch:** Any authenticated farm owner iterates candidate emails through `POST /api/team/workers`. `201` ⇒ email is not registered anywhere on the platform; `400 CANT_ADD_TO_TEAM` ⇒ a global account exists. This converts team management into a platform-wide account-existence scanner (useful pre-credential-stuffing, or to learn that a person is a Herdly user).

**Impact:** Information disclosure of account existence across all tenants, by any owner-tier principal. Mitigations in code (verified): each probe pays a full Argon2id hash (`team.py:866-869`, before the existence check) and charges the actor's shared team-password sliding-window budget (`_hash_team_password`, `team.py:173-228`, default `auth_rate_limit_max_attempts=10` per window, `core/config.py:245`), plus a one-concurrent-per-owner reservation. Effective rate ≈ 10 probes/window/owner. The route's own docstring (`team.py:800-807`) acknowledges the invitation-flow gap.

**Fix:** Introduce the documented email-verified invitation flow, or (interim) return the generic 400 for *every* create after hashing a dummy password for fresh emails too — i.e., always respond `400 CANT_ADD_TO_TEAM` unless the invitation is accepted, with identical timing for both branches.

## RT-B-2 — Low — B9 — Owner-side status path never revalidates target role liveness

**Evidence:**
- `backend/app/api/team.py:599-600` — `_locked_membership`: `if user.id != farm.owner_id: await _pin_membership_role(db, farm, membership)` — the Membership→Role SHARE re-pin runs **only for non-owner actors**.
- `backend/app/api/team.py:1246-1254` — `delete_role` locks the role, checks `role.code in ROLE_PRESET_CODES`, then `_member_count` and the actionable-duty guard, then tombstones.

**Analysis:** An owner `PUT /workers/{id}/status` with `is_active=true` therefore never checks that the membership's `role_id` still resolves to a live role; `_get_membership` (`team.py:441-472`) selectinloads `role` without a `deleted_at` filter. If a membership whose role was tombstoned were reactivated, the worker would get a zombie "active" roster row while every subsequent request fails closed (`deps.active_membership` requires `Role.deleted_at IS NULL`, `deps.py:360-374` → 404). The state is currently **unreachable through the API** because `delete_role`'s `_member_count` (`team.py:640-647`) counts memberships of live users regardless of `is_active`, so any live-user membership blocks role deletion; it could only arise from manual DB damage or a future change to that guard. No privilege escalation is possible — the failure mode is fail-closed lockout plus a cosmetically stale `role_name` in the owner's response (unlocked selectinload).

**Fix:** In `_locked_membership`, pin the target role for the owner as well (or at minimum re-assert role liveness on reactivation), keeping the Membership→User→Role order unchanged for owners (they hold no membership row, so no new cycle is introduced).

## RT-B-3 — Low — B9/B10/B11 — No durable audit trail for team administration

**Evidence:** `api/team.py` writes no audit rows for `reset_password` (mutation at `team.py:1113-1118`), `set_worker_status`, `change_role`, `create_worker`, `create_role`/`update_role`/`delete_role`. The only security logging in the file is throttling (`team.py:164-170`) and `deps.require_perm` denials (`deps.py:646-650`). The `User` row records `must_change_password`/`token_version` but never *who* reset a credential; `FarmMembership` has no `updated_by`/history.

**Impact:** An owner (or a delegated manager, for the actions within their ceiling) can reset a provisioned worker's password, deactivate workers, or rewrite role permissions with no tamper-evident record. Disputes about insider lockout/hijack ("the manager locked me out", "someone emptied the Feeder role's permissions") cannot be reconstructed from the database. The `must_change_password` fence partially compensates for resets only.

**Fix:** Emit an append-only audit event (actor id, farm id, action, target, before/after) inside the same transaction as each team mutation; reuse the retention pattern already used for idempotency records.

## RT-B-4 — Low — B1 — Non-canonical integer spellings accepted for `X-Farm-Id`

**Evidence:** `deps.py:536-540` — `farm_id = int(x_farm_id)` guarded only by `0 < farm_id < 2**62`. Empirically, Python `int()` accepts `"٥"`/`"５"` (Unicode digits → 5), `"+5"`, `" 5 "`, `"05"`, and `"5_0"` (→ 50); rejects `"0x5"` and `"5, 5"` (ValueError → 400). Duplicate headers are rejected by the `getlist` check (`deps.py:533-534`), and FastAPI's singular `Header()` binding would otherwise take the first occurrence (verified in `fastapi/dependencies/utils.py::_get_multidict_value`).

**Analysis:** Authorization is performed on the *resolved* integer against the same farm row, so there is no bypass: `X-Farm-Id: ٥` reaches exactly farm 5 with identical 404/permission outcomes. The residual issue is canonicalization hygiene: edge WAFs/validators that assume `[0-9]+` may treat these spellings as non-numeric (and strip or specially route them), creating proxy/application disagreement on a security-relevant header — the exact class of ambiguity the duplicate-header check exists to prevent.

**Fix:** Parse with a strict `^[0-9]+$` regex before `int()`, returning the same 400.

## RT-B-5 — Info — B4 — Lock-free read authorization window (design)

`deps.current_user` is lock-free by design (`deps.py:170-178`), and GET/HEAD/OPTIONS worker requests authorize via unlocked `active_membership` (`deps.py:556-564`, `610-619`). A deactivation, role reassignment, or role-permission edit that commits while a read is in flight (or just before it starts) is not visible to that request. Every subsequent request re-derives the bundle (`farm.owner_id == user.id` and membership/role are re-read per request — no cross-request caching; sessions are per-request, `db.py:97-100`). Mutating methods pin Membership→User→Role with `populate_existing` (`deps.py:406-467`, `572-582`), so no stale-authorization *write* is possible. Held as an accepted, documented tradeoff.

## RT-B-6 — Info — B5 — `active_membership(lock_authorization=True)` is dead code

**Evidence:** `deps.py:346-403` implements a locked variant (Membership SHARE → Role SHARE). The only two call sites are `deps.py:557` and `deps.py:614`, both defaulting to `lock_authorization=False`. Production pinning instead goes through `_lock_membership_row` → `_pin_authenticated_user` → `_lock_membership_role` (`deps.py:572-579`), which additionally revalidates the authenticated User between the Membership and Role locks. The dead variant omits that middle step; a future caller adopting it for an unsafe path would silently skip the token_version/deleted_at re-pin. Recommend deleting the flag and branch or aligning it with the canonical chain.

## RT-B-7 — Info — B12 — Roster discloses global emails/user ids to `team.manage` holders

**Evidence:** `team.py:725-787` (`team_page`) returns `MembershipOut(email=membership.user.email, user_id=...)` for the whole roster; `_membership_out` (`team.py:400-423`) does the same on every mutation response. A delegated manager sees workers' global identity even though those accounts may also belong to other farms. `can_reset_password` is correctly masked to `(False, OWNER_ONLY)` for non-owner viewers (`team.py:407-412`). Inherent to the delegated-administration model; noted per scope instruction, no action suggested beyond the audit-trail finding above.

## RT-B-8 — Info — B12 — Overflow 409 can strand roster-based management

**Evidence:** `team.py:750-767` — if live memberships exceed `max_team_members_per_farm` (or roles exceed `max_roles_per_farm`), `GET /api/team` raises 409 `TEAM_RESPONSE_OVERFLOW_REASON`, telling the owner to "archive legacy memberships". Deactivation requires a `membership_id` (`PUT /workers/{id}/status`), which the owner can no longer discover from the API (the roster is the only listing). Reachable only by lowering the configured cap below an existing farm's size (import/legacy data or config change). The per-actor mutation endpoints themselves remain functional with known ids. Suggest documenting a support path or returning the first N ids with the 409.

---

## Per-unit attacked-&-held notes

### B1 — Farm scoping (`deps.current_farm`)
- **Duplicate/case/whitespace headers:** `getlist("x-farm-id") != 1` → 400 (`deps.py:533-534`); case-insensitive via Starlette normalization; comma-joined proxy form fails `int()` → 400 (`deps.py:536-538`); FastAPI's own singular binding takes the first value, but the explicit check fires first (verified in FastAPI `utils.py:758-761`). **Held.**
- **Range handling:** `0 < farm_id < 2**62` rejects `0`, negatives, 2^62+ (`deps.py:539-540`); `farm_id <= MAX_INT32_ID` guard prevents asyncpg int4 DataError 500s (`deps.py:541-543`, bound `schemas/common.py:19`). **Held.**
- **Unknown vs forbidden id:** both paths raise byte-identical `404 {"detail":"Farm not found"}` (`deps.py:547-548` unknown; `deps.py:558-559`/`573-574`/`577-578` no-membership/no-role). Forbidden-existing performs 1–2 extra indexed queries vs unknown — sub-millisecond residual timing only (accepted). **Held.**
- **Header on auth routes:** only routes declaring `CurrentFarm` consume it (`/api/auth/permissions`); `GET/POST /api/auth/farms` ignore it (`auth.py:1472-1562`). **Held.**
- Non-canonical integer spellings: see RT-B-4 (no authz impact).

### B2 — Farm creation (`auth.create_farm`)
- **Quota race:** `User FOR UPDATE` acquired and token_version-exact-validated *before* `execute_idempotent`, whose `mutate()` performs the COUNT (`auth.py:1509-1531`) — same-actor concurrent creates serialize on the user row; the comment's KEY-SHARE-upgrade deadlock is thereby also prevented. **Held.**
- **Atomicity:** `seed_new_farm` (roles + inventory, `seed.py:500-503`) runs inside the same transaction; any seeding error rolls back farm, seed rows, and the idempotency claim together (`services/idempotency.py:448-450` catches `BaseException` → rollback). No partial farm. **Held.**
- **Idempotency claim actor-scoping:** unique partial index `(actor_id, operation, key_digest) WHERE farm_id IS NULL` + CHECK tying NULL farm to the farm-create operation (`models/idempotency.py:48-64`); replay across actors uses distinct rows (safe); same key different payload → 409 hash mismatch (`idempotency.py:378-382`). **Held.**
- **must_change_password fence:** `POST /api/auth/farms` is not in `_ROTATION_EXEMPT_ALWAYS`; `/api/auth/farms` is exempt **GET-only** (`deps.py:201-222`), so creation from a flagged credential returns 403 (`deps.py:187-194`). **Held.** No X-Farm-Id requirement on the route (correct).

### B3 — Farm listing/switching
- `accessible_farms` caps owned at `cap+1` → 409, then memberships at `remaining+1` → 409; joins live roles only; owned-first ordering (`deps.py:470-517`). Switching is stateless per-request re-derivation via the header (no server-side "current farm" to corrupt). `GET /farms` needs no farm header (`test_auth_extended.py:3098` pins). **Held.**

### B4 — Permission resolution
- Owner ⇒ `ALL_PERMISSIONS` computed from `farm.owner_id == user.id` on the per-request-loaded farm (`deps.py:625-631`) — no caching staleness. Worker ⇒ `role.permission_set()`.
- **Corrupt JSONB fails closed:** `models/core.py:165-182` — JSON parse error or non-list shape (incl. `"null"`, bare string) → logged warning + empty set; plus DB CHECK `jsonb_typeof(permissions) = 'array'` (`models/core.py:117-119`). **Held.**
- **Deleted role with active membership:** `active_membership`'s EXISTS requires `Role.deleted_at IS NULL` (`deps.py:360-368`) → 404 (uniform). Composite FK `(farm_id, role_id)` (`models/core.py:199-205`) blocks cross-farm role attach at the storage layer. **Held.**
- **`require_perm` denials:** exact set-membership on full permission strings (`deps.py:643-653`); `test_rbac_exhaustive.py:159,223,243` pins that every route (incl. team) carries a guard and 403s a zero-permission worker. **Held.** Read-path stale window: RT-B-5.

### B5 — Dependency-chain locks
- Canonical worker-mutation order Membership(SHARE, of=) → User(SHARE, of=) → Role(SHARE, of=) enforced in `current_farm` (`deps.py:572-579`) with `populate_existing` everywhere (the documented identity-map pitfall is handled at `deps.py:421,439,464` and in `team.py:466-467,491-494,1104-1106` — the reset path's lost-update comment is explicitly mitigated).
- Owner-reset path: actor-User SHARE (`team.py:333-344`) → Membership NO KEY UPDATE (`team.py:588-594`) → target-User UPDATE (`team.py:1093-1107`) — same Membership→User direction; actor is always the owner (no membership row), so no cycle with worker paths; verified against `change_role`/`set_worker_status`/`create_worker`/refresh (User→RefreshSession) — no opposite-order path found. Role-only routes (`update_role`/`delete_role` take Role NO KEY UPDATE and never lock Membership afterwards) keep the graph acyclic; the cross race with `change_role` fails closed (role re-read includes `deleted_at IS NULL`).
- Team/task share-lock interplay pinned by `test_lock_order_hardening.py:815,1198,1270,1335,1611,1672,1725,1842`. Dead code variant: RT-B-6.

### B6 — Permission catalog/presets
- 27 permissions; every `.manage` maps to its `.view` in `PERMISSION_DEPENDENCIES` (`permissions.py:51-66`); server-side enforcement in `_clean_permissions` (`team.py:716-721`) on both create and update. Preset bundles all satisfy dependencies (checked per preset, `permissions.py:105-261`). `TASK_CATEGORY_ROLE_MAP` codes ⊆ `ROLE_PRESET_CODES`. **Held.**

### B7 — Worker provisioning
- **Owner-only before any tenant read:** `_prepare_worker_create` 403s non-owners (`team.py:271-272`); the only preceding reads are the caller's own authorization bundle. **Held.**
- **Pre-existing account:** one generic message on all three account kinds (`team.py:900-905`); password is hashed on **both** branches before the existence check (`team.py:866-869` precedes `mutate()`), so no Argon timing difference; email *format* 422s happen at validation, independent of existence. Existence oracle via status code: RT-B-1.
- **Password policy:** same `password_policy_error` as self-service (`team.py:853-856`, `security.py:135-142`); `must_change_password=True` on the provisioned user (`team.py:911-913`). **Held.**
- **Capacity:** preflight count is advisory (`team.py:231-254`); the binding count runs under the per-farm advisory xact lock + Farm KEY SHARE pin (`team.py:650-707, 879-885`) — race-safe. **Held.**
- **Idempotency:** only the keyed HMAC-SHA-256 fingerprint of the canonical request is persisted for `team.workers.create` (`services/idempotency.py:30, 159-175`); raw password never enters the record or response; rotation keys verified with constant-time multi-candidate compare (`idempotency.py:178-191`). In-process gate keyed `(farm, actor, sha256(key))` (`team.py:97-133`); same key on a different farm yields a distinct DB claim (unique index includes `farm_id`) and distinct gate — both execute on their own farms, which is safe. Contention arbitrated by `ON CONFLICT DO NOTHING` + contender re-read (`idempotency.py:323-399`). **Held.**

### B8 — Worker role reassignment
- **Self-service:** preflight (`team.py:974-976`) and locked (`team.py:989-995`) both block changing one's own role. **Held.**
- **Peer-manager:** `_guard_peer_manager` runs on both `change_role` and `set_worker_status` (POST role: `team.py:977`, `601`; PUT status: `team.py:1047`, `601`), evaluated on the *pinned* role under locks for non-owners (`team.py:599-601`) — a concurrent promotion of the target to a team.manage role is re-guarded after the wait (pinned by `test_lock_order_hardening.py:1842`). **Held.**
- **Delegated ceiling:** `_guard_role_scope` uses exact `set.issubset` per permission (`team.py:618-630`), applied to both the target's current role and the new role, owner-exempt. **Held.**
- **team.manage grant:** `_guard_manager_role` on assignment (`team.py:1002`, `899`), `_guard_manager_permission` on role create/update payloads (`team.py:1139, 1197`) — critical because team.manage *is* inside a manager's own ceiling and would otherwise be grantable; both fire on the raw payload/locked role. **Held.**
- **Cross-farm/tombstoned role:** `_get_role` filters `farm_id` + `deleted_at` → 404→400 "Pick a valid role." (`team.py:979-983, 997-1001`); composite FK backs it. **Held.**
- **Revision:** full-role edits guarded by `expected_revision` under the row lock → 409 stale (`team.py:1189-1196`). **Held.**

### B9 — Worker status changes
- **Retired toggle:** route still registered (`include_in_schema=False`) and answers `405` with `Allow: PUT` (`team.py:1015-1028`) — not a 404. **Held.**
- **PUT status:** self-deactivation blocked twice (`team.py:1042-1046, 1049-1055`); peer-manager + ceiling guards re-run under locks (`team.py:1047-1048, 1056`); assigning the requested value makes transport retries idempotent (`team.py:1057-1063`). Reactivation of a tombstoned-role membership is blocked for managers (`_pin_membership_role` → 404) and unreachable for owners (see RT-B-2). **Held.**
- **No session revocation — verified tenant-local by design:** the deactivated worker's access token stays valid for identity routes but every farm-scoped request re-checks membership (`deps.py:556-564` read, `572-578` write) → uniform 404; their global refresh families survive for other farms (`test_team_extended.py:1416,1502,2045` pin this). **Held.**

### B10 — Owner worker-password reset
- **Owner-only:** 403 in `_prepare_password_reset` before membership reads (`team.py:297-298`). **Held.**
- **Policy contract:** `_reset_password_policy` requires active AND provisioned AND !owns_farm AND !has_other_membership in one bounded EXISTS query (`team.py:357-397`; other-membership includes inactive rows — conservative); re-validated under Membership + target-User FOR UPDATE after hashing (`team.py:1089-1112`), with the User lock blocking concurrent foreign-affiliation inserts (FK KEY SHARE) — the serialization claim verified. **Held.**
- **Generic failure message:** one `RESET_PASSWORD_SELF_SERVICE_REASON` covers owns-farm/other-membership/not-provisioned — no oracle about the target's affiliations (`team.py:363-367`). **Held.**
- **Post-hash reauthorization:** `_reauthorize_prepared_owner` re-pins the exact actor snapshot (User FOR SHARE + `token_version == snapshot` + deleted check) and re-checks farm ownership (`team.py:325-354`); actor cannot be swapped mid-hash — any concurrent credential event on the owner bumps token_version and fails the pin. **Held.**
- **Owner's own membership / other farm's worker:** owner has no membership → 404; cross-farm membership id → farm-scoped 404 "Membership not found" (`team.py:441-472`). **Held.**
- **Effects:** `password_hash` swap + `token_version += 1` + `must_change_password=True` + `revoke_user_sessions` under the target-User FOR UPDATE (`team.py:1113-1118`), with `populate_existing` preventing the stale identity-map lost update (`team.py:1100-1106`). Refresh's User→RefreshSession order cannot mint a successor post-revocation. **Held.**

### B11 — Custom role CRUD
- **Server-owned `code`:** `RoleIn`/`RoleUpdateIn` extend `StrictInputModel` (`extra="forbid"`, `schemas/common.py:29-37`) → injected `code` is a 422; create hardcodes `code=None` (`team.py:1158-1164`); update never writes `code`; DB CHECK bounds the vocabulary (`models/core.py:110-113`). A preset therefore cannot be re-coded into deletability, and custom roles can't masquerade as presets. **Held.**
- **Capacity:** advisory-lock count for roles (`team.py:1133-1138, 680-706`). **Held.**
- **Dependency validation:** `_clean_permissions` 400s action-without-view (`team.py:716-721`). **Held.**
- **Non-owner escalation via preserve:** final set = `(raw ∩ ALL ∩ actor_perms) ∪ (existing − actor_perms)` (`team.py:709-715, 1216-1218`) — a manager can never *add* beyond their ceiling, cannot strip beyond-ceiling perms, and team.manage roles are wholly untouchable (`_guard_manager_role` on the locked row, `team.py:1190-1191`); concurrent owner edits force a 409 via revision. **Held.**
- **PUT revision:** checked under FOR NO KEY UPDATE → 409 (`team.py:1189-1196`). **Held.**
- **Preset undeletable by stored `code`** (`team.py:1249-1251`); `code` immutable (above). **Held.**
- **Delete guards:** 0 live members (`team.py:1252-1255`; counts live-user memberships regardless of `is_active`, so inactive holders also block) and no actionable duties — `requires_action_clause` = PENDING ∨ (DONE ∧ verification-required category) (`models/tasks.py:188-194`); skipped/verified/completed non-verification duties do not block and remain as tombstoned FK history (`team.py:1256-1273`). **Held.**

### B12 — Team page aggregate
- Roster strictly farm-scoped (`FarmMembership.farm_id == farm.id` + live users, `team.py:731-750`); no cross-farm users reachable. `can_reset_password` masked for non-owner viewers with one generic reason (`team.py:407-412`); owner sees the same single self-service reason for all three disqualifiers. Overflow 409 for both memberships and roles (`team.py:750-767`); role catalog exposes labels/codes only. Global emails: RT-B-7; overflow lockout: RT-B-8.

### Cross-cutting
- **Worker without team.manage reaching any team endpoint:** every route (incl. both prepared-dependency routes and the retired toggle) resolves `TEAM_PERM`; `test_rbac_exhaustive.py` mechanically pins coverage for the whole router set. **Held.**
- **Cross-farm membership/role ids:** every lookup is farm-filtered with int4 bounds and returns the uniform 404 (`team.py:441-472, 475-500`); `test_team_extended.py:1372,1701,2004` pin it. **Held.**
- **Membership/role write surface:** only `api/team.py` (plus `seed.py` preset seeding) constructs `FarmMembership`/`Role` rows — no side door. **Held.**
- **must_change_password fence vs team routes:** all `/api/team/*` are non-exempt → a provisioned/reset worker cannot act until self-service rotation (`deps.py:187-194`); only bootstrap GETs (`me/permissions/farms`) and the rotation routes are allowed. **Held.**

## Verdict

The tenancy/RBAC/team surface is consistently enforced with fail-closed semantics, exact per-permission ceilings, a linear lock graph, and replay-safe idempotency. No authorization bypass, cross-tenant access, or privilege escalation was found. The one Medium (account-existence oracle via worker creation) is a known architectural consequence of the missing invitation flow and is rate-bounded; the remaining findings are hardening and observability gaps.
