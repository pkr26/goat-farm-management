"""Mutant catalogs, one function per campaign. See harness.py for semantics."""

from __future__ import annotations

import harness  # noqa: F401  (path setup side effect when imported after harness)

from harness import Mutant

A = "backend/app"
T = "backend/tests"

# Cross-cutting selections reused by campaign 1.
TENANCY_CORE = [
    f"{T}/test_tenant_foreign_keys.py",
    f"{T}/test_rbac_exhaustive.py",
    f"{T}/test_adversarial.py",
]


def _m(mid, desc, patches, targeted, full_check=True):
    return Mutant(campaign="c1", mid=mid, description=desc, patches=patches, targeted=targeted, full_check=full_check)


def c1_tenancy_rbac():
    M = []
    add = M.append

    # ---- per-router tenant-filter deletion: DETAIL (IDOR) queries ----
    add(_m("animals-idor", "animals: drop farm filter from get/update/delete-by-id",
           {f"{A}/api/animals.py": [
               {"op": "replace", "count": 2,
                "old": "stmt = select(Animal).where(Animal.id == animal_id, Animal.farm_id == farm_id)",
                "new": "stmt = select(Animal).where(Animal.id == animal_id)"}]},
           [f"{T}/test_animals_extended.py", f"{T}/test_animals_bugs.py", *TENANCY_CORE]))
    add(_m("breeding-idor", "breeding: drop farm filter from record-by-id",
           {f"{A}/api/breeding.py": [
               {"op": "replace",
                "old": ".where(BreedingRecord.id == record_id, BreedingRecord.farm_id == farm.id)",
                "new": ".where(BreedingRecord.id == record_id)"}]},
           [f"{T}/test_breeding_extended.py", f"{T}/test_breeding_bugs.py", *TENANCY_CORE]))
    add(_m("feeding-idor", "feeding: drop farm filter from inventory item by id",
           {f"{A}/api/feeding.py": [
               {"op": "replace",
                "old": ".where(FeedInventory.id == item_id, FeedInventory.farm_id == farm.id)",
                "new": ".where(FeedInventory.id == item_id)"}]},
           [f"{T}/test_feeding_extended.py", *TENANCY_CORE]))
    add(_m("finance-idor", "finance: drop farm filter from purchase-batch lookup by id",
           {f"{A}/api/finance.py": [
               {"op": "replace",
                "old": ".where(PurchaseBatch.id == txn.source_id, PurchaseBatch.farm_id == farm.id)",
                "new": ".where(PurchaseBatch.id == txn.source_id)"}]},
           [f"{T}/test_finance_extended.py", f"{T}/test_finance_bugs.py", *TENANCY_CORE]))
    add(_m("health-idor", "health: drop farm filter from animal-by-id in health routes",
           {f"{A}/api/health.py": [
               {"op": "regex", "mode": "first", "at_least": 1,
                "pattern": r"select\(Animal\)\.where\(Animal\.id == animal_id, Animal\.farm_id == farm\.id\)",
                "repl": "select(Animal).where(Animal.id == animal_id)"}]},
           [f"{T}/test_health_extended.py", f"{T}/test_health_bugs.py", *TENANCY_CORE]))
    add(_m("screening-idor", "screening: drop farm filter from image-by-id",
           {f"{A}/api/screening.py": [
               {"op": "replace",
                "old": ".where(ScreeningImage.farm_id == farm.id, ScreeningImage.id == image_id)",
                "new": ".where(ScreeningImage.id == image_id)"}]},
           [f"{T}/test_screening.py", *TENANCY_CORE]))
    add(_m("tasks-idor", "tasks: drop farm filter from task-by-id (get/patch/complete)",
           {f"{A}/api/tasks.py": [
               {"op": "replace", "count": 3,
                "old": ".where(Task.id == task_id, Task.farm_id == farm.id)",
                "new": ".where(Task.id == task_id)"}]},
           [f"{T}/test_tasks_extended.py", f"{T}/test_tasks_bugs.py", *TENANCY_CORE]))
    add(_m("simulation-idor", "simulation: drop farm filter from scenario-by-id",
           {f"{A}/api/simulation.py": [
               {"op": "regex", "mode": "all", "count": 3,
                "pattern": r"\.where\(SimulationScenario\.farm_id == farm\.id\)",
                "repl": ".where(True)"}]},
           [f"{T}/test_simulation_api.py", *TENANCY_CORE]))

    # ---- per-router tenant-filter deletion: LIST queries ----
    add(_m("animals-list", "animals: drop farm filter from animal list query",
           {f"{A}/api/animals.py": [
               {"op": "replace",
                "old": "stmt = select(Animal).where(Animal.farm_id == farm.id)",
                "new": "stmt = select(Animal)"}]},
           [f"{T}/test_animals_extended.py", f"{T}/test_animal_profile_pagination.py", *TENANCY_CORE]))
    add(_m("breeding-list", "breeding: drop farm filter from list filter expression",
           {f"{A}/api/breeding.py": [
               {"op": "replace",
                "old": "where = BreedingRecord.farm_id == farm.id",
                "new": "where = BreedingRecord.id > 0"}]},
           [f"{T}/test_breeding_extended.py", *TENANCY_CORE]))
    add(_m("buckets-list", "buckets: drop farm filter from bucket-board animal query",
           {f"{A}/api/buckets.py": [
               {"op": "replace",
                "old": ".where(Animal.farm_id == farm.id, Animal.status == AnimalStatus.ACTIVE.value)",
                "new": ".where(Animal.status == AnimalStatus.ACTIVE.value)"}]},
           [f"{T}/test_animals_extended.py", f"{T}/test_dashboard_redteam.py", *TENANCY_CORE]))
    add(_m("buckets-settings", "buckets: drop farm filter from bucket feed settings list",
           {f"{A}/api/buckets.py": [
               {"op": "replace",
                "old": "select(BucketFeedSetting).where(BucketFeedSetting.farm_id == farm.id)",
                "new": "select(BucketFeedSetting)"}]},
           [f"{T}/test_animals_extended.py", *TENANCY_CORE]))
    add(_m("dashboard-list", "dashboard: drop farm filter from summary animal query",
           {f"{A}/api/dashboard.py": [
               {"op": "regex", "mode": "first", "at_least": 1,
                "pattern": r"( *)Animal\.farm_id == farm_id,\n",
                "repl": ""}],
            },
           [f"{T}/test_dashboard_permissions.py", f"{T}/test_dashboard_redteam.py", *TENANCY_CORE]))
    add(_m("feeding-list", "feeding: drop farm filter from feeding records list",
           {f"{A}/api/feeding.py": [
               {"op": "replace",
                "old": "stmt = select(FeedingRecord).where(FeedingRecord.farm_id == farm.id)",
                "new": "stmt = select(FeedingRecord)"}]},
           [f"{T}/test_feeding_extended.py", *TENANCY_CORE]))
    add(_m("finance-list", "finance: drop farm filter from transactions list",
           {f"{A}/api/finance.py": [
               {"op": "regex", "mode": "first", "at_least": 1,
                "pattern": r"( *)Transaction\.farm_id == farm_id,\n",
                "repl": ""}],
            },
           [f"{T}/test_finance_extended.py", f"{T}/test_finance_bugs.py", *TENANCY_CORE]))
    add(_m("health-list", "health: drop farm filter from health-events animal scoping",
           {f"{A}/api/health.py": [
               {"op": "regex", "mode": "first", "at_least": 2,
                "pattern": r"( *)Animal\.farm_id == farm\.id,\n",
                "repl": ""}],
            },
           [f"{T}/test_health_extended.py", *TENANCY_CORE]))
    add(_m("kidding-list", "kidding: drop farm filter from kidding records list",
           {f"{A}/api/kidding.py": [
               {"op": "replace",
                "old": ".where(KiddingRecord.farm_id == farm.id)",
                "new": ".where(KiddingRecord.id > 0)", "count": 2}]},
           [f"{T}/test_kidding_husbandry.py", f"{T}/test_kidding_due_pagination.py", *TENANCY_CORE]))
    add(_m("planner-list", "planner: drop farm filter from plan list/count",
           {f"{A}/api/planner.py": [
               {"op": "regex", "mode": "all", "count": 3,
                "pattern": r"\.where\(PlannerPlan\.farm_id == farm\.id\)",
                "repl": ".where(PlannerPlan.id > 0)"},
               {"op": "regex", "mode": "all", "count": 2,
                "pattern": r" *(PlannerPlan\.farm_id == farm_id),\n",
                "repl": ""}],
            },
           [f"{T}/test_planner_api.py", *TENANCY_CORE]))
    add(_m("purchases-list", "purchases: drop farm filter from purchase batch list",
           {f"{A}/api/purchases.py": [
               {"op": "replace",
                "old": "base = select(PurchaseBatch).where(PurchaseBatch.farm_id == farm.id)",
                "new": "base = select(PurchaseBatch)"}]},
           [f"{T}/test_purchase_batch_bulk.py", *TENANCY_CORE]))
    add(_m("screening-list", "screening: drop farm filter from image list filters",
           {f"{A}/api/screening.py": [
               {"op": "replace",
                "old": "filters = [ScreeningImage.farm_id == farm.id]",
                "new": "filters = []"}]},
           [f"{T}/test_screening.py", *TENANCY_CORE]))
    add(_m("team-list", "team: drop farm filter from member list",
           {f"{A}/api/team.py": [
               {"op": "regex", "mode": "first", "at_least": 1,
                "pattern": r"\.where\(FarmMembership\.farm_id == farm\.id, User\.deleted_at\.is_\(None\)\)",
                "repl": ".where(User.deleted_at.is_(None))"}]},
           [f"{T}/test_team_extended.py", f"{T}/test_team_bugs.py", *TENANCY_CORE]))
    add(_m("team-roles", "team: drop farm filter from role list",
           {f"{A}/api/team.py": [
               {"op": "regex", "mode": "first", "at_least": 1,
                "pattern": r"( *)Role\.farm_id == farm\.id,\n",
                "repl": ""}],
            },
           [f"{T}/test_team_extended.py", f"{T}/test_rbac.py", *TENANCY_CORE]))
    add(_m("auth-farmscope", "auth/deps: farm selector lists ALL farms, not just owned",
           {f"{A}/deps.py": [
               {"op": "replace",
                "old": "select(Farm).where(Farm.owner_id == user.id).order_by(Farm.id).limit(cap + 1)",
                "new": "select(Farm).order_by(Farm.id).limit(cap + 1)"}]},
           [f"{T}/test_auth_extended.py", *TENANCY_CORE]))
    add(_m("ops-sim-limits", "ops_simulation: run-limits keyed globally instead of per-farm",
           {f"{A}/api/ops_simulation.py": [
               {"op": "replace",
                "old": "return await _with_run_limits(farm_id, user_id, run)",
                "new": "return await _with_run_limits(1, user_id, run)"}]},
           [f"{T}/test_daily_ops_api.py", f"{T}/test_ops.py", *TENANCY_CORE]))

    # ---- central gate: X-Farm-Id membership enforcement in deps.current_farm ----
    add(_m("central-membership-bypass", "deps.current_farm: non-owner gains any farm without membership",
           {f"{A}/deps.py": [
               {"op": "replace",
                "old": "    unsafe_request = request.method not in {\"GET\", \"HEAD\", \"OPTIONS\"}",
                "new": "    unsafe_request = False"},
               {"op": "regex", "mode": "first", "at_least": 1,
                "pattern": r"    if not unsafe_request:\n        membership = await active_membership\(db, user\.id, farm\.id\)\n        if membership is None:\n            raise HTTPException\(status_code=404, detail=\"Farm not found\"\)",
                "repl": "    if not unsafe_request:\n        membership = await active_membership(db, user.id, farm.id)\n        if membership is None:\n            return farm"}]},
           [f"{T}/test_rbac.py", f"{T}/test_rbac_exhaustive.py", f"{T}/test_tenant_foreign_keys.py", f"{T}/test_adversarial.py", f"{T}/test_dashboard_permissions.py"]))

    # ---- RBAC mutants ----
    add(_m("rbac-require-perm-bypass", "require_perm: permission check never denies",
           {f"{A}/deps.py": [
               {"op": "replace",
                "old": "        if code not in perms:",
                "new": "        if False and code not in perms:"}]},
           [f"{T}/test_rbac.py", f"{T}/test_rbac_exhaustive.py", f"{T}/test_dashboard_permissions.py"]))
    add(_m("rbac-viewer-write", "VIEWER (Auditor) role preset gains finance.manage write access",
           {f"{A}/permissions.py": [
               {"op": "replace",
                "old": ('            "simulation.view",\n'
                        '            "reports.view",\n        ],\n    },\n]'),
                "new": ('            "simulation.view",\n'
                        '            "reports.view",\n'
                        '            "finance.manage",\n        ],\n    },\n]')}]},
           [f"{T}/test_rbac.py", f"{T}/test_rbac_exhaustive.py", f"{T}/test_dashboard_permissions.py"]))
    add(_m("rbac-cleaner-verify", "CLEANER role preset gains tasks.verify (can self-verify duties)",
           {f"{A}/permissions.py": [
               {"op": "replace",
                "old": ('        "permissions": ["dashboard.view", "tasks.view", "tasks.complete"],\n'
                        '    },\n    {\n        "code": "CLEANER_MANAGER",'),
                "new": ('        "permissions": ["dashboard.view", "tasks.view", "tasks.complete", "tasks.verify"],\n'
                        '    },\n    {\n        "code": "CLEANER_MANAGER",')}]},
           [f"{T}/test_rbac.py", f"{T}/test_rbac_exhaustive.py", f"{T}/test_security_workflow.py"]))
    add(_m("rbac-perms-for-owner", "perms_for: any farm member gets the full owner permission set",
           {f"{A}/deps.py": [
               {"op": "replace",
                "old": "    if farm.owner_id == user.id:\n        return set(ALL_PERMISSIONS)\n    if membership is None or membership.role is None:\n        return set()\n    return membership.role.permission_set()",
                "new": "    if farm.owner_id == user.id:\n        return set(ALL_PERMISSIONS)\n    if membership is None or membership.role is None:\n        return set()\n    return set(ALL_PERMISSIONS)"}]},
           [f"{T}/test_rbac.py", f"{T}/test_rbac_exhaustive.py", f"{T}/test_dashboard_permissions.py"]))
    add(_m("rbac-perm-dependency-drop", "PERMISSION_DEPENDENCIES: manage-without-view allowed",
           {f"{A}/permissions.py": [
               {"op": "regex", "mode": "all", "count": 7,
                "pattern": r'    "[a-z.]+\.manage": "[a-z.]+\.view",\n',
                "repl": ""}]},
           [f"{T}/test_rbac.py", f"{T}/test_rbac_exhaustive.py", f"{T}/test_team_extended.py"]))

    return M


def _m2(mid, desc, patches, targeted, full_check=True):
    return Mutant(campaign="c2", mid=mid, description=desc, patches=patches, targeted=targeted, full_check=full_check)


def c2_husbandry():
    M = []
    add = M.append
    HB = f"{A}/models/helpers.py"
    BR = f"{A}/services/breeding.py"
    SP = f"{A}/models/species.py"
    AN = f"{A}/services/animals.py"
    KD = f"{A}/services/kidding.py"
    DO = f"{A}/simulation/daily_ops.py"
    KID_T = [f"{T}/test_kidding_husbandry.py", f"{T}/test_breeding_extended.py",
             f"{T}/test_kidding_due_pagination.py", f"{T}/test_domain_chronology.py"]
    BRED_T = [f"{T}/test_breeding_extended.py", f"{T}/test_breeding_bugs.py",
              f"{T}/test_animals_status_husbandry.py", f"{T}/test_logic.py"]

    # -- 150-day gestation: mutate the consumption, not the constant --
    add(_m2("ekd-149", "expected_kidding_date uses gestation 149 (breeding+149)",
           {HB: [{"op": "replace",
                  "old": "return breeding_date + timedelta(days=GOAT_PROFILE.gestation_days)",
                  "new": "return breeding_date + timedelta(days=GOAT_PROFILE.gestation_days - 1)"}]},
           KID_T))
    add(_m2("ekd-151", "expected_kidding_date uses gestation 151 (breeding+151)",
           {HB: [{"op": "replace",
                  "old": "return breeding_date + timedelta(days=GOAT_PROFILE.gestation_days)",
                  "new": "return breeding_date + timedelta(days=GOAT_PROFILE.gestation_days + 1)"}]},
           KID_T))

    # -- ultrasound day 32: scheduled date +/-1 and the sim's < vs <= boundary --
    add(_m2("us-day-31", "planned ultrasound date is breeding+31 (day 32 -> 31)",
           {HB: [{"op": "replace",
                  "old": "return breeding_date + timedelta(days=GOAT_PROFILE.pregnancy_check_after_service_days)",
                  "new": "return breeding_date + timedelta(days=GOAT_PROFILE.pregnancy_check_after_service_days - 1)"}]},
           BRED_T))
    add(_m2("us-day-33", "planned ultrasound date is breeding+33 (day 32 -> 33)",
           {HB: [{"op": "replace",
                  "old": "return breeding_date + timedelta(days=GOAT_PROFILE.pregnancy_check_after_service_days)",
                  "new": "return breeding_date + timedelta(days=GOAT_PROFILE.pregnancy_check_after_service_days + 1)"}]},
           BRED_T))
    add(_m2("us-sim-boundary", "daily-ops pregnancy check boundary < -> <= (day 32 itself skipped)",
           {DO: [{"op": "replace",
                  "old": "if gestation_day is None or gestation_day < _PROFILE.pregnancy_check_after_service_days:",
                  "new": "if gestation_day is None or gestation_day <= _PROFILE.pregnancy_check_after_service_days:"}]},
           [f"{T}/test_daily_ops.py", f"{T}/test_simulation_goat_domain.py"]))

    # -- weaning day 60: +/-1 on the WEANING duty date --
    add(_m2("wean-59", "weaning duty scheduled at kidding+59 (60 -> 59)",
           {KD: [{"op": "replace", "count": 2,
                  "old": "kidding_date + timedelta(days=profile.weaning_days)",
                  "new": "kidding_date + timedelta(days=profile.weaning_days - 1)"}]},
           KID_T))
    add(_m2("wean-61", "weaning duty scheduled at kidding+61 (60 -> 61)",
           {KD: [{"op": "replace", "count": 2,
                  "old": "kidding_date + timedelta(days=profile.weaning_days)",
                  "new": "kidding_date + timedelta(days=profile.weaning_days + 1)"}]},
           KID_T))

    # -- dry-off / rest-flush 10-day window: < -> <= and 9/11 --
    add(_m2("rest-lte", "RESTING re-entry boundary < -> <= (day-10 doe refused re-entry)",
           {AN: [{"op": "replace",
                  "old": "and (when - resting_since).days < profile.min_rest_flush_days",
                  "new": "and (when - resting_since).days <= profile.min_rest_flush_days"}]},
           BRED_T))
    add(_m2("rest-9", "rest-and-flush window shortened to 9 days",
           {AN: [{"op": "replace",
                  "old": "and (when - resting_since).days < profile.min_rest_flush_days",
                  "new": "and (when - resting_since).days < profile.min_rest_flush_days - 1"}]},
           BRED_T))
    add(_m2("rest-11", "rest-and-flush window stretched to 11 days",
           {AN: [{"op": "replace",
                  "old": "and (when - resting_since).days < profile.min_rest_flush_days",
                  "new": "and (when - resting_since).days < profile.min_rest_flush_days + 1"}]},
           BRED_T))

    # -- breeding readiness: age AND weight -> OR; off-by-one on each --
    add(_m2("ready-and-to-or", "is_breeding_candidate: (age gate AND weight gate) -> OR",
           {BR: [{"op": "replace",
                  "old": ("        and age is not None\n"
                          "        and age >= profile.min_breeding_age_months\n"
                          "        and latest_weight_kg is not None\n"
                          "        and latest_weight_kg >= profile.min_breeding_weight_kg\n"),
                  "new": ("        and (age is not None and age >= profile.min_breeding_age_months\n"
                          "             or latest_weight_kg is not None\n"
                          "             and latest_weight_kg >= profile.min_breeding_weight_kg)\n"),
                  "count": 1}]},
           BRED_T))
    add(_m2("ready-age-lt-e", "breeding age floor >= -> > (12-month-and-a-day doe admitted)",
           {BR: [{"op": "replace",
                  "old": "        and age >= profile.min_breeding_age_months",
                  "new": "        and age > profile.min_breeding_age_months"}]},
           BRED_T))
    add(_m2("ready-age-11mo", "breeding age floor effectively 11 months (12 -> 11)",
           {BR: [{"op": "replace",
                  "old": "        and age >= profile.min_breeding_age_months",
                  "new": "        and age >= profile.min_breeding_age_months - 1"}]},
           BRED_T))
    add(_m2("ready-weight-gt", "breeding weight floor >= -> > (exactly-22kg doe refused)",
           {BR: [{"op": "replace",
                  "old": "        and latest_weight_kg >= profile.min_breeding_weight_kg",
                  "new": "        and latest_weight_kg > profile.min_breeding_weight_kg"}]},
           BRED_T))
    add(_m2("ready-weight-21", "breeding weight floor 22kg -> 21kg",
           {BR: [{"op": "replace",
                  "old": "        and latest_weight_kg >= profile.min_breeding_weight_kg",
                  "new": "        and latest_weight_kg >= profile.min_breeding_weight_kg - 1.0"}]},
           BRED_T))

    # -- 45-day quarantine: early release, off-by-one, guards dropped --
    add(_m2("quar-release-39", "quarantine release duty moved from day 45 to day 39",
           {HB: [{"op": "replace",
                  "old": ('    (\n        45,\n        TaskCategory.BUCKET_MOVE,\n'
                          '        "quarantine_release",'),
                  "new": ('    (\n        39,\n        TaskCategory.BUCKET_MOVE,\n'
                          '        "quarantine_release",')}]},
           [f"{T}/test_purchase_batch_bulk.py", f"{T}/test_ops.py", f"{T}/test_unit_bugs.py"]))
    add(_m2("quar-off-by-one", "quarantine schedule due dates shifted one day early (offset-1 -> offset-2)",
           {HB: [{"op": "replace",
                  "old": "due_date = batch.date + timedelta(days=day_offset - 1)",
                  "new": "due_date = batch.date + timedelta(days=day_offset - 2)"}]},
           [f"{T}/test_purchase_batch_bulk.py", f"{T}/test_unit_bugs.py", f"{T}/test_logic.py"]))
    add(_m2("quar-guard-date-drop", "release guard no longer checks the duty matches the day-45 protocol date",
           {f"{A}/services/tasks.py": [{"op": "replace",
                  "old": 'if release_spec is None or task.due_date != release_spec["due_date"]:',
                  "new": 'if release_spec is None:'}]},
           [f"{T}/test_tasks_extended.py", f"{T}/test_purchase_batch_bulk.py"]))

    # -- inbreeding fence: drop halves, shallow walk --
    add(_m2("inbreed-parent-off", "inbreeding fence: parent-offspring pairing allowed",
           {BR: [{"op": "replace",
                  "old": "        if (\n            buck_is_doe_parent\n            or doe_is_buck_parent\n            or full_siblings",
                  "new": "        if (\n            False\n            or False\n            or full_siblings"}]},
           BRED_T))
    add(_m2("inbreed-full-sib", "inbreeding fence: full-sibling pairing allowed",
           {BR: [{"op": "replace",
                  "old": "            or full_siblings\n            or buck.id in doe_grandparents",
                  "new": "            or False\n            or buck.id in doe_grandparents"}]},
           BRED_T))
    add(_m2("inbreed-shallow", "inbreeding fence: pedigree walk truncated (grandparent/avuncular checks dropped)",
           {BR: [{"op": "replace",
                  "old": ("            or buck.id in doe_grandparents\n"
                          "            or doe.id in buck_grandparents\n"
                          "            or avuncular\n"),
                  "new": ("            or False\n            or False\n            or False\n"),
                  "count": 1}]},
           BRED_T))
    return M


def _m3(mid, desc, patches, targeted, full_check=True):
    return Mutant(campaign="c3", mid=mid, description=desc, patches=patches, targeted=targeted, full_check=full_check)


def c3_biosecurity():
    M = []
    add = M.append
    HS = [f"{T}/test_health_safety.py", f"{T}/test_health_extended.py"]
    LC = [f"{T}/test_e2e_lifecycle_audit.py", f"{T}/test_animals_status_husbandry.py",
          f"{T}/test_animals_bugs.py", f"{T}/test_domain_check_constraints.py"]

    add(_m3("restrict-fail-open", "suspected scheduled disease no longer sets movement_restricted (fail-open)",
           {f"{A}/services/health.py": [{"op": "replace",
                  "old": "    animal.suspected_scheduled_disease = True\n    animal.suspected_disease = target\n    animal.movement_restricted = True",
                  "new": "    animal.suspected_scheduled_disease = True\n    animal.suspected_disease = target\n    animal.movement_restricted = False"}]},
           HS))
    add(_m3("disease-flag-fail-open", "suspected disease hold flag itself never set (fail-open)",
           {f"{A}/services/health.py": [{"op": "replace",
                  "old": "    animal.suspected_scheduled_disease = True",
                  "new": "    animal.suspected_scheduled_disease = False"}]},
           HS))
    add(_m3("move-while-restricted", "move_animal allows bucket moves for restricted animals (guard dropped)",
           {f"{A}/services/animals.py": [{"op": "replace",
                  "old": ("    if (\n        animal.movement_restricted or animal.suspected_scheduled_disease\n"
                          "    ) and not allow_restricted_reclassification:\n        return\n"),
                  "new": "",
                  "count": 1}]},
           LC))
    add(_m3("buck-escapes-breeding", "lifecycle: manual BREEDING->FOUNDATION edge added (buck escapes terminal residency)",
           {f"{A}/models/lifecycle.py": [{"op": "replace",
                  "old": '    (Bucket.FOUNDATION.value, Bucket.BREEDING.value): frozenset({"manual", "breeding"}),',
                  "new": ('    (Bucket.FOUNDATION.value, Bucket.BREEDING.value): frozenset({"manual", "breeding"}),\n'
                          '    (Bucket.BREEDING.value, Bucket.FOUNDATION.value): frozenset({"manual"}),')}]},
           LC))
    add(_m3("sex-pen-guard-drop", "bucket guard: sex checks for MALE_KIDS/FEMALE_KIDS pens dropped",
           {f"{A}/services/animals.py": [{"op": "replace",
                  "old": ('    if to_bucket == Bucket.MALE_KIDS.value and animal.sex != "M":\n'
                          '        return "Only male animals may enter MALE_KIDS"\n'
                          '    if to_bucket == Bucket.FEMALE_KIDS.value and animal.sex != "F":\n'
                          '        return "Only female animals may enter FEMALE_KIDS"\n'),
                  "new": ""}]},
           LC))
    add(_m3("weaning-wrong-pen", "weaning routes every kid to FEMALE_KIDS regardless of sex",
           {f"{A}/services/tasks.py": [{"op": "replace",
                  "old": 'return Bucket.MALE_KIDS.value if animal.sex == "M" else Bucket.FEMALE_KIDS.value',
                  "new": 'return Bucket.FEMALE_KIDS.value'},
                 {"op": "replace",
                  "old": 'target = Bucket.MALE_KIDS.value if kid.sex == "M" else Bucket.FEMALE_KIDS.value',
                  "new": 'target = Bucket.FEMALE_KIDS.value'}]},
           LC + [f"{T}/test_tasks_extended.py"]))
    add(_m3("quar-release-hold-drop", "quarantine release proceeds despite movement restriction/disease hold",
           {f"{A}/services/tasks.py": [{"op": "replace",
                  "old": ("    if any(animal.movement_restricted or animal.suspected_scheduled_disease for animal in animals):\n"
                          '        raise ValueError(\n'
                          '            "Quarantine release is blocked by a recorded movement restriction or disease hold"\n'
                          "        )\n"),
                  "new": ""}]},
           [f"{T}/test_purchase_batch_bulk.py", f"{T}/test_tasks_extended.py", f"{T}/test_health_safety.py"]))
    return M







def _mk(campaign):
    def _m(mid, desc, patches, targeted, full_check=True):
        return Mutant(campaign=campaign, mid=mid, description=desc, patches=patches,
                      targeted=targeted, full_check=full_check)
    return _m


def c4_bucket_machine():
    _m = _mk("c4")
    AN = f"{A}/services/animals.py"
    KD = f"{A}/services/kidding.py"
    LC = [f"{T}/test_e2e_lifecycle_audit.py", f"{T}/test_animals_status_husbandry.py",
          f"{T}/test_animals_bugs.py", f"{T}/test_domain_chronology.py"]
    WT = LC + [f"{T}/test_kidding_husbandry.py", f"{T}/test_tasks_extended.py"]
    return [
        _m("transition-check-drop", "any bucket edge accepted for any context (state machine guard dropped)",
           {AN: [{"op": "replace",
                  "old": ('    allowed_contexts = LEGAL_BUCKET_TRANSITIONS.get((animal.current_bucket, to_bucket))\n'
                          '    if allowed_contexts is None or context not in allowed_contexts:\n'
                          '        return (\n'
                          '            f"Illegal lifecycle transition {animal.current_bucket} → {to_bucket}; "\n'
                          '            "use the required breeding, health, kidding or quarantine workflow"\n'
                          "        )\n"),
                  "new": ""}]},
           LC),
        _m("skip-stage-edge", "lifecycle: BREEDING->DELIVERY direct edge added (skip PREGNANCY stages)",
           {f"{A}/models/lifecycle.py": [{"op": "replace",
                  "old": '    (Bucket.PREGNANCY_LATE.value, Bucket.DELIVERY.value): frozenset({"manual", "delivery"}),',
                  "new": ('    (Bucket.PREGNANCY_LATE.value, Bucket.DELIVERY.value): frozenset({"manual", "delivery"}),\n'
                          '    (Bucket.BREEDING.value, Bucket.DELIVERY.value): frozenset({"manual"}),')}]},
           LC),
        _m("no-current-bucket-update", "move_animal records the BucketMove but never updates animal.current_bucket",
           {AN: [{"op": "replace",
                  "old": ("            reason=reason or None,\n"
                          "            created_by_id=created_by_id,\n"
                          "        )\n"
                          "    )\n"
                          "    animal.current_bucket = to_bucket\n"),
                  "new": ("            reason=reason or None,\n"
                          "            created_by_id=created_by_id,\n"
                          "        )\n"
                          "    )\n")}]},
           LC),
        _m("no-move-record", "move_animal updates current_bucket but records no BucketMove row (history lost)",
           {AN: [{"op": "replace",
                  "old": ("    db.add(\n"
                          "        BucketMove(\n"
                          "            animal_id=animal.id,\n"
                          "            from_bucket=animal.current_bucket,\n"
                          "            to_bucket=to_bucket,\n"
                          "            effective_date=reference_date or today(),\n"
                          "            reason=reason or None,\n"
                          "            created_by_id=created_by_id,\n"
                          "        )\n"
                          "    )\n"),
                  "new": ""}]},
           LC),
        _m("effective-date-ignored", "BucketMove always stamped today(), ignoring the backdated reference_date",
           {AN: [{"op": "replace",
                  "old": "            effective_date=reference_date or today(),",
                  "new": "            effective_date=today(),"}]},
           LC),
        _m("weaning-task-dropped", "kidding: the +60d WEANING duty is never generated",
           {KD: [{"op": "replace",
                  "old": ("    if alive_count:\n"
                          "        await _add_task(\n"
                          "            db,\n"
                          "            farm.id,\n"
                          "            f\"Wean kids of {doe.tag_number}; doe → RESTING\","),
                  "new": ("    if alive_count and False:\n"
                          "        await _add_task(\n"
                          "            db,\n"
                          "            farm.id,\n"
                          "            f\"Wean kids of {doe.tag_number}; doe → RESTING\",")}]},
           WT),
        _m("weaning-task-duplicated", "kidding: the WEANING duty is generated twice per kidding",
           {KD: [{"op": "replace",
                  "old": ("    if alive_count:\n"
                          "        await _add_task(\n"
                          "            db,\n"
                          "            farm.id,\n"
                          "            f\"Wean kids of {doe.tag_number}; doe → RESTING\","),
                  "new": ("    if alive_count:\n"
                          "        for _wean_once in (0, 1):\n"
                          "            await _add_task(\n"
                          "            db,\n"
                          "            farm.id,\n"
                          "            f\"Wean kids of {doe.tag_number}; doe → RESTING\",")}]},
           WT),
        _m("weaning-anchor-breeddate", "WEANING duty anchored to breeding date instead of kidding date",
           {KD: [{"op": "regex", "mode": "all", "count": 2,
                  "pattern": r"kidding_date \+ timedelta\(days=profile\.weaning_days\)",
                  "repl": "breeding_date + timedelta(days=profile.weaning_days)"}]},
           WT),
    ]


def c5_verification():
    _m = _mk("c5")
    TK = f"{A}/api/tasks.py"
    VT = [f"{T}/test_security_workflow.py", f"{T}/test_tasks_extended.py",
          f"{T}/test_tasks_bugs.py", f"{T}/test_rbac_exhaustive.py"]
    return [
        _m("self-verify-allowed", "two-person rule dropped: the completer may verify their own duty",
           {TK: [{"op": "replace",
                  "old": ("    if task.completed_by_id == user.id and farm.owner_id != user.id:\n"
                          '        raise HTTPException(status_code=409, detail="Someone else must verify this duty")'),
                  "new": ("    if False:\n"
                          '        raise HTTPException(status_code=409, detail="Someone else must verify this duty")')}]},
           VT),
        _m("owner-exemption-flip", "owner exemption inverted: only the owner is blocked from verifying",
           {TK: [{"op": "replace",
                  "old": "    if task.completed_by_id == user.id and farm.owner_id != user.id:",
                  "new": "    if task.completed_by_id == user.id and farm.owner_id == user.id:"}]},
           VT),
        _m("verify-before-complete", "a PENDING duty can be verified without ever being completed",
           {TK: [{"op": "replace",
                  "old": ("    if task.status != TaskStatus.DONE.value or not task.needs_verification:\n"
                          '        raise HTTPException(status_code=400, detail="Task is not awaiting verification")\n'
                          "    # Two-person rule"),
                  "new": ("    if task.status == TaskStatus.SKIPPED.value or not task.needs_verification:\n"
                          '        raise HTTPException(status_code=400, detail="Task is not awaiting verification")\n'
                          "    # Two-person rule")}]},
           VT),
        _m("double-verify", "an already-VERIFIED duty can be verified again",
           {TK: [{"op": "replace",
                  "old": ("    if task.status != TaskStatus.DONE.value or not task.needs_verification:\n"
                          '        raise HTTPException(status_code=400, detail="Task is not awaiting verification")\n'
                          "    # Two-person rule"),
                  "new": ("    if task.status not in (TaskStatus.DONE.value, TaskStatus.VERIFIED.value) "
                          "or not task.needs_verification:\n"
                          '        raise HTTPException(status_code=400, detail="Task is not awaiting verification")\n'
                          "    # Two-person rule")}]},
           VT),
        _m("cleaning-role-mismatch", "auto-task routing: CLEANING duties assigned to the VET role",
           {f"{A}/permissions.py": [{"op": "replace",
                  "old": '    "CLEANING": "CLEANER",',
                  "new": '    "CLEANING": "VET",'}]},
           VT + [f"{T}/test_ops.py"]),
        _m("verification-categories-empty", "VERIFICATION_REQUIRED_CATEGORIES emptied: no category requires verification",
           {f"{A}/models/constants.py": [{"op": "replace",
                  "old": 'VERIFICATION_REQUIRED_CATEGORIES = (TaskCategory.CLEANING.value,)',
                  "new": 'VERIFICATION_REQUIRED_CATEGORIES = ()'}]},
           VT),
    ]


def c6_auth_tokens():
    _m = _mk("c6")
    AT = f"{A}/api/auth.py"
    SEC = f"{A}/security.py"
    AUTH_T = [f"{T}/test_auth_bugs.py", f"{T}/test_auth_extended.py",
              f"{T}/test_jwt_rotation.py", f"{T}/test_auth_token_version_races.py"]
    TOTP_T = [f"{T}/test_totp.py", f"{T}/test_security_hardening.py"]
    return [
        _m("replay-no-revocation", "replayed refresh token revokes nothing (family kill removed)",
           {AT: [{"op": "replace",
                  "old": ("        await revoke_session_family(db, session.family_id, user_id=session.user_id)\n"
                          "        await db.commit()"),
                  "new": "        await db.commit()"}]},
           AUTH_T),
        _m("replay-single-session", "replay revokes only the replayed session, not the family",
           {AT: [{"op": "replace",
                  "old": "        await revoke_session_family(db, session.family_id, user_id=session.user_id)",
                  "new": "        session.revoked_at = now"}]},
           AUTH_T),
        _m("cookie-httponly-off", "refresh cookie set without HttpOnly",
           {AT: [{"op": "regex", "mode": "first", "at_least": 2,
                  "pattern": r"        httponly=True,",
                  "repl": "        httponly=False,"}]},
           AUTH_T),
        _m("cookie-samesite-none", "refresh cookie SameSite relaxed to none",
           {AT: [{"op": "regex", "mode": "first", "at_least": 2,
                  "pattern": r'        samesite="lax",',
                  "repl": '        samesite="none",'}]},
           AUTH_T),
        _m("cookie-secure-off", "refresh cookie Secure flag forced off",
           {AT: [{"op": "regex", "mode": "all", "at_least": 3,
                  "pattern": r"secure=s\.cookie_secure,|secure=settings\.cookie_secure,|secure=get_settings\(\)\.cookie_secure,",
                  "repl": "secure=False,"}]},
           AUTH_T),
        _m("access-expiry-lt", "access-token expiry boundary <= -> < (token valid one extra instant)",
           {AT: [{"op": "replace",
                  "old": "    if claims.expires_at <= utcnow():",
                  "new": "    if claims.expires_at < utcnow():"}]},
           AUTH_T),
        _m("session-expiry-lt", "refresh-session expiry boundary <= -> <",
           {AT: [{"op": "replace",
                  "old": "    if session.expires_at <= now:",
                  "new": "    if session.expires_at < now:"}]},
           AUTH_T),
        _m("totp-rotation-previous-dropped", "TOTP rekey: previous-key ring dropped (rotated rows cannot decrypt)",
           {SEC: [{"op": "replace",
                  "old": "        for index, key in enumerate((current, *previous)):",
                  "new": "        for index, key in enumerate((current,)):"}]},
           TOTP_T),
        _m("totp-rewrap-skipped", "TOTP rekey: needs_rewrap always False (rows never rewrapped to current key)",
           {SEC: [{"op": "replace",
                  "old": "            return DecryptedTotpSecret(secret=secret, needs_rewrap=index > 0)",
                  "new": "            return DecryptedTotpSecret(secret=secret, needs_rewrap=False)"}]},
           TOTP_T),
        _m("rejected-login-timing-off", "rejected-login timing padding disabled (user enumeration oracle)",
           {SEC: [{"op": "replace",
                  "old": ("def _complete_rejected_login_timing(\n"
                          "    password: str, stored: str, dummy_hash: str, did_argon_work: bool\n"
                          ") -> None:"),
                  "new": ("def _complete_rejected_login_timing(\n"
                          "    password: str, stored: str, dummy_hash: str, did_argon_work: bool\n"
                          ") -> None:\n"
                          "    return")}]},
           [f"{T}/test_security_hardening.py", f"{T}/test_password_capacity.py", f"{T}/test_auth_bugs.py"]),
        _m("legacy-rehash-stall", "legacy pbkdf2 verified but never upgraded to Argon2id",
           {AT: [{"op": "replace",
                  "old": ("        if replacement_hash is not None:  # legacy pbkdf2 → Argon2id\n"
                          "            user.password_hash = replacement_hash"),
                  "new": ("        if False and replacement_hash is not None:  # legacy pbkdf2 → Argon2id\n"
                          "            user.password_hash = replacement_hash")}]},
           [f"{T}/test_auth_bugs.py", f"{T}/test_auth_extended.py"]),
    ]


def c7_idempotency():
    _m = _mk("c7")
    IDEM = f"{A}/services/idempotency.py"
    IT = [f"{T}/test_idempotency.py", f"{T}/test_redteam_remediation_2026_09_04.py",
          f"{T}/test_prefarm_idempotency_migration.py"]
    return [
        _m("fingerprint-ignored", "request fingerprint mismatch no longer 409s (same key replays across bodies)",
           {IDEM: [{"op": "replace",
                  "old": "    if operation not in SENSITIVE_IDEMPOTENCY_OPERATIONS:\n        return stored_hash == candidates[0]",
                  "new": "    if True:\n        return True"}]},
           IT),
        _m("scope-cross-farm", "replay lookup drops the farm_id scope (key replay across tenants)",
           {IDEM: [{"op": "regex", "mode": "all", "count": 2,
                  "pattern": r"                IdempotencyRecord\.farm_id == farm_id,\n",
                  "repl": ""}]},
           IT),
        _m("scope-cross-actor", "replay lookup drops the actor_id scope (key replay across users)",
           {IDEM: [{"op": "regex", "mode": "all", "count": 3,
                  "pattern": r"^ *IdempotencyRecord\.actor_id == actor_id,\n",
                  "repl": ""}]},
           IT),
        _m("expiry-boundary", "retention boundary <= -> < (expired record replayed one extra instant)",
           {IDEM: [{"op": "replace",
                  "old": "    if existing is None or existing.expires_at <= utcnow():",
                  "new": "    if existing is None or existing.expires_at < utcnow():"}]},
           IT),
    ]




def campaign_mutants(name: str) -> list[Mutant]:
    return CAMPAIGNS[name]()

def c8_screening():
    _m = _mk("c8")
    ROT = f"{A}/services/screening/rotation.py"
    PIPE = f"{A}/services/screening/pipeline.py"
    SCR = f"{A}/api/screening.py"
    ST = [f"{T}/test_screening.py"]
    return [
        _m("rotation-frozen", "date-based provider rotation pinned to the first provider",
           {ROT: [{"op": "replace",
                  "old": "        return self._providers[on.toordinal() % len(self._providers)]",
                  "new": "        return self._providers[0]"}]},
           ST),
        _m("failover-removed", "detect fallback chain reduced to the primary provider only",
           {PIPE: [{"op": "replace",
                  "old": "    chain = [primary, *rotation.fallbacks_for(primary)]",
                  "new": "    chain = [primary]"}]},
           ST),
        _m("error-backoff-removed", "ERROR rows retried immediately (hourly backoff -> zero)",
           {PIPE: [{"op": "replace",
                  "old": "ERROR_RETRY_AFTER = dt.timedelta(hours=1)",
                  "new": "ERROR_RETRY_AFTER = dt.timedelta(0)"}]},
           ST),
        _m("gate-exhausted-healthy", "gate exhaustion filed as HEALTHY instead of ERROR (silent skip)",
           {PIPE: [{"op": "replace",
                  "old": "        return ScreeningImageStatus.ERROR.value\n    # The gate chain is the longest single provider segment (every rotation",
                  "new": "        return ScreeningImageStatus.HEALTHY.value\n    # The gate chain is the longest single provider segment (every rotation"}]},
           ST),
        _m("specialists-skipped", "flagged photos never reach specialists (gate observations become findings)",
           {PIPE: [{"op": "replace",
                  "old": ("        if serving is None:\n"
                          "            continue"),
                  "new": ("        if True:\n"
                          "            continue")}]},
           ST),
        _m("export-status-filter-off", "dataset export ignores vet_status filter (pending always included)",
           {SCR: [{"op": "replace",
                  "old": ('    if vet_status != "ALL":\n'
                          '        filters.append(ScreeningFinding.status == vet_status)'),
                  "new": ('    if False:\n'
                          '        filters.append(ScreeningFinding.status == vet_status)')}]},
           ST),
        _m("content-dedup-off", "normalized-content reservation disabled (same photo screened twice)",
           {PIPE: [{"op": "replace",
                  "old": ("    owner_id = await _content_claim_owner(db, image.farm_id, sha256)\n"
                          "    if owner_id is not None:"),
                  "new": ("    owner_id = None\n"
                          "    if owner_id is not None:")}]},
           ST),
    ]


def c9_simulation():
    _m = _mk("c9")
    MC = f"{A}/simulation/montecarlo.py"
    PL = f"{A}/simulation/planner.py"
    EN = f"{A}/simulation/engine.py"
    MK = f"{A}/simulation/market.py"
    SIMT = [f"{T}/test_simulation_engine.py", f"{T}/test_simulation_deep_mutation.py",
            f"{T}/test_simulation_financials.py", f"{T}/test_simulation_finance_mutation.py"]
    PLT = SIMT + [f"{T}/test_simulation_planner.py", f"{T}/test_backward_planner.py"]
    return [
        _m("mc-seed-ignored", "Monte Carlo RNG seeded from OS entropy instead of the run seed",
           {MC: [{"op": "replace",
                  "old": "    rng = random.Random(a.risk.seed)",
                  "new": "    rng = random.Random()"}]},
           [f"{T}/test_simulation_engine.py", f"{T}/test_simulation_audit_remediation.py",
            f"{T}/test_simulation_advanced.py"]),
        _m("planner-lead-minus-gestation", "backward planner: gestation subtracted from the lead window",
           {PL: [{"op": "replace",
                  "old": ("        class_max_age\n"
                          "        + assumptions.reproduction.gestation_months\n"
                          "        + assumptions.herd.purchased_doe_settling_months\n"
                          "        + 1"),
                  "new": ("        class_max_age\n"
                          "        - assumptions.reproduction.gestation_months\n"
                          "        + assumptions.herd.purchased_doe_settling_months\n"
                          "        + 1")}]},
           PLT),
        _m("planner-settling-off-by-one", "backward planner lead window shortened by one month",
           {PL: [{"op": "replace",
                  "old": ("        + assumptions.herd.purchased_doe_settling_months\n"
                          "        + 1"),
                  "new": ("        + assumptions.herd.purchased_doe_settling_months\n"
                          "        + 0")}]},
           PLT),
        _m("parity-stillbirth-sign", "stillbirth rate applied as a birth-rate bonus instead of a loss",
           {PL: [{"op": "replace", "count": 2,
                  "old": "r.litter_size * (1.0 - r.stillbirth_rate)",
                  "new": "r.litter_size * (1.0 + r.stillbirth_rate)"}]},
           PLT),
        _m("growth-premium-sign-flip", "young male weight premium subtracted instead of added",
           {EN: [{"op": "replace",
                  "old": "        return weight * (1.0 + growth.young_male_weight_premium)",
                  "new": "        return weight * (1.0 - growth.young_male_weight_premium)"}]},
           SIMT),
        _m("feed-take-sign-flip", "feed utilization factor: take added instead of subtracted",
           {EN: [{"op": "replace",
                  "old": "        factor = 1.0 - take / available",
                  "new": "        factor = 1.0 + take / available", "count": 2}]},
           SIMT),
        _m("festival-uplift-dropped", "Eid festival price uplift never applied",
           {MK: [{"op": "replace",
                  "old": "    uplift = 1.0 + sales.eid_price_uplift if festival else 1.0",
                  "new": "    uplift = 1.0"}]},
           SIMT + [f"{T}/test_simulation_redteam.py", f"{T}/test_bakrid_advisory.py"]),
    ]


def c10_contract():
    _m = _mk("c10")
    OAPI = "shared/openapi.json"
    CT = [f"{T}/test_contract_drift.py"]
    return [
        _m("openapi-type-change", "shared/openapi.json: FarmOut.name type string -> integer",
           {OAPI: [{"op": "replace",
                  "old": ('      "FarmOut": {\n'
                          '        "properties": {\n'
                          '          "id": {\n'
                          '            "type": "integer",\n'
                          '            "title": "Id"\n'
                          '          },\n'
                          '          "name": {\n'
                          '            "type": "string",\n'
                          '            "title": "Name"\n'
                          '          },'),
                  "new": ('      "FarmOut": {\n'
                          '        "properties": {\n'
                          '          "id": {\n'
                          '            "type": "integer",\n'
                          '            "title": "Id"\n'
                          '          },\n'
                          '          "name": {\n'
                          '            "type": "integer",\n'
                          '            "title": "Name"\n'
                          '          },')}]},
           CT, full_check=False),
        _m("openapi-schema-dropped", "shared/openapi.json: entire FarmOut schema removed",
           {OAPI: [{"op": "regex", "mode": "first", "at_least": 1,
                  "pattern": r'      "FarmOut": \{[\s\S]*?\n      \},\n',
                  "repl": ""}]},
           CT, full_check=False),
        _m("backend-field-optional", "Pydantic FarmOut.location gains a description (live schema drifts from snapshot)",
           {f"{A}/schemas/auth.py": [{"op": "replace",
                  "old": "    location: str | None",
                  "new": "    location: str | None = Field(default=None, description='location')",
                  "count": 2}]},
           CT + [f"{T}/test_schema_parity.py"], full_check=False),
    ]

CAMPAIGNS = {
    "c1": c1_tenancy_rbac,
    "c2": c2_husbandry,
    "c3": c3_biosecurity,
    "c4": c4_bucket_machine,
    "c5": c5_verification,
    "c6": c6_auth_tokens,
    "c7": c7_idempotency,
    "c8": c8_screening,
    "c9": c9_simulation,
    "c10": c10_contract,
}
