"""Generate and run the 38 business-domain campaigns with isolated receipts.

Generic mutants and reviewed semantic specifications share the existing runner.
The catalog owns each site once; import-time sites use the explicitly declared
domain suite rather than a sampled three-file fallback. All counts are reported,
including uncovered, invalid and inconclusive attempts.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

import mutate_gen
from mutate_identity import (
    atomic_json,
    digest_json,
    input_identity,
    latest_compatible,
    read_results,
    sha_file,
)
from mutate_run import BACKEND as BACKEND
from mutate_run import MUTDIR, Runner

NAMES = [
    "Animal registration, identity and weighing",
    "Bucket lifecycle and history corrections",
    "Procurement and quarantine",
    "Mating eligibility, ancestry and sire capacity",
    "Pregnancy diagnosis, loss and failed cycles",
    "Kidding, newborn identity and immediate care",
    "Weaning, orphans and maternal recovery",
    "Sales, deaths, culling and exit cascades",
    "Clinical records and vaccination schedules",
    "Herd health-round completeness",
    "Disease holds, clearance and withdrawal",
    "Ration planning and feeding progress",
    "Feed inventory, mixing and dispensing",
    "Cash ledger corrections and source reconciliation",
    "Insurance policies, premiums and claims",
    "Actual P&L and memo valuations",
    "Duty lifecycle and verification",
    "Duty recurrence, assignment and capacity",
    "Husbandry calendar and cadence",
    "Notification recipients, digest and alert policy",
    "Screening capture, batches and upload admission",
    "Image normalization and evidence integrity",
    "Multi-goat detection and clinical coverage",
    "Clinical cascade and provider rotation",
    "Screening queue, retries and paid-call budgets",
    "Veterinary adjudication and review history",
    "Provider quality statistics and training corpus",
    "Forecast herd biology and animal conservation",
    "Forecast feed, fodder and water",
    "Forecast markets, festivals and revenue",
    "Forecast financing and project valuation",
    "Seeded risk and sensitivity",
    "Farm optimization",
    "Backward sale-target planning",
    "Daily operations simulation",
    "Farm-history calibration",
    "Saved scenarios and plans",
    "Dashboard, reports and owner analytics",
]

# Exhaustive selections for import-time/otherwise untraced sites. Function sites
# use every measured coverer, plus any explicitly specified semantic oracles.
TESTS = {
    1: [
        "test_animals_extended",
        "test_animals_bugs",
        "test_domain_chronology",
        "test_animal_profile_pagination",
        "test_mutation38_quarantine_overflow",
        "test_mutation38_quarantine_cached_task",
        "test_mutation38_quarantine_cached_batch",
    ],
    2: ["test_lifecycle_gaps", "test_lifecycle_attribution_gaps", "test_animals_status_husbandry"],
    3: [
        "test_purchase_batch_bulk",
        "test_health_extended",
        "test_health_safety",
        "test_finance_gaps",
    ],
    4: [
        "test_breeding_extended",
        "test_breeding_gaps",
        "test_redteam_domain_fixes",
        "test_e2e_scenario_gaps",
        "test_adversarial_regressions",
    ],
    5: ["test_breeding_extended", "test_breeding_bugs", "test_domain_audit_fixes"],
    6: ["test_kidding_husbandry", "test_kidding_gaps", "test_kidding_suffix_gaps"],
    7: ["test_breeding_bugs", "test_breeding_extended", "test_lifecycle_gaps"],
    8: ["test_animals_status_husbandry", "test_breeding_extended", "test_chronology_mutation_gaps"],
    9: ["test_health_extended", "test_health_safety", "test_domain_audit_fixes"],
    10: ["test_health_safety", "test_independent_domain_audit"],
    11: ["test_health_safety", "test_health_gaps", "test_redteam_domain_fixes"],
    12: ["test_feeding_extended", "test_feeding_split_gaps", "test_feeding_gaps"],
    13: ["test_feeding_extended", "test_feed_quantity_numeric", "test_feeding_gaps"],
    14: [
        "test_finance_extended",
        "test_finance_gaps",
        "test_independent_ad2f616_domain",
        "test_finance_bugs",
    ],
    15: ["test_finance_insurance", "test_domain_check_constraints"],
    16: ["test_finance_insurance", "test_finance_extended", "test_owner_overview"],
    17: ["test_tasks_extended", "test_tasks_bugs", "test_tasks_gaps"],
    18: ["test_tasks_extended", "test_tasks_bugs", "test_task_visibility_parity"],
    19: ["test_cadence", "test_tasks_gaps"],
    20: [
        "test_notifications",
        "test_notifications_mutation_gaps",
        "test_notification_delivery_integrity",
    ],
    21: ["test_screening"],
    22: ["test_screening", "test_screening_clinical_integrity"],
    23: ["test_screening", "test_screening_clinical_integrity"],
    24: ["test_screening", "test_screening_clinical_integrity", "test_screening_provider_gaps"],
    25: ["test_screening", "test_screening_clinical_integrity"],
    26: [
        "test_screening",
        "test_screening_clinical_integrity",
        "test_independent_review_history_audit",
    ],
    27: ["test_screening", "test_screening_clinical_integrity"],
    28: ["test_simulation_engine", "test_simulation_financials", "test_simulation_goat_domain"],
    29: ["test_simulation_financials", "test_simulation_advanced", "test_simulation_goat_domain"],
    30: ["test_simulation_engine", "test_simulation_advanced"],
    31: [
        "test_simulation_financials",
        "test_simulation_finance_mutation",
        "test_decision_audit_regressions",
    ],
    32: [
        "test_simulation_advanced",
        "test_simulation_audit_remediation",
        "test_simulation_integrity",
    ],
    33: ["test_simulation_advanced", "test_simulation_audit_remediation"],
    34: ["test_simulation_planner", "test_backward_planner", "test_simulation_redteam"],
    35: ["test_daily_ops", "test_daily_ops_api", "test_ops_migration_integrity"],
    36: ["test_calibration_curve_math", "test_simulation_api", "test_cull_price_calibration"],
    37: ["test_simulation_api", "test_planner_api", "test_simulation_integrity"],
    38: [
        "test_dashboard_redteam",
        "test_dashboard_permissions",
        "test_bakrid_advisory",
        "test_owner_overview",
    ],
}


def suite(domain: int) -> list[str]:
    paths = [f"tests/{name}.py" for name in TESTS[domain]]
    group = "livestock" if domain <= 16 else "workflow" if domain <= 27 else "simulation"
    paths.extend(
        extra.relative_to(BACKEND).as_posix()
        for extra in sorted((BACKEND / "tests").glob(f"test_mutation38_{group}*.py"))
    )
    if 17 <= domain <= 27:
        for name in ("test_mutation38_crop_priority.py", "test_mutation38_budget_midnight.py"):
            extra = BACKEND / "tests" / name
            if extra.exists():
                paths.append(extra.relative_to(BACKEND).as_posix())
    progress = BACKEND / "tests/test_000_mutation38_simulation_progress.py"
    if domain >= 28 and progress.exists():
        paths.append(progress.relative_to(BACKEND).as_posix())
    return list(dict.fromkeys(paths))


def symbol(tree: ast.AST, line: int) -> str:
    enclosing = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.lineno <= line <= (node.end_lineno or node.lineno)
    ]
    return (
        min(enclosing, key=lambda n: (n.end_lineno or n.lineno) - n.lineno).name.lower()
        if enclosing
        else "module"
    )


def contract_oracles(file: str, tree: ast.Module, line: int) -> list[str]:
    """Attach public contracts through outer functions as well as inner scopes."""
    profile_history_oracle = (
        "tests/test_animal_profile_pagination.py::"
        "test_profile_histories_have_exact_totals_independent_pages_and_constant_queries"
    )
    enclosing = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.lineno <= line <= (node.end_lineno or node.lineno)
    }
    mappings = {
        ("app/api/animals.py", "create_animal"): (
            "tests/test_mutation38_livestock_birth_date_precedence.py",
            "tests/test_mutation38_livestock_purchase_entry_dates.py",
            "tests/test_mutation38_livestock_birth_weight_messages.py",
            "tests/test_mutation38_livestock_individual_purchase_graph.py",
            "tests/test_mutation38_livestock_purchase_move_audit.py",
            "tests/test_mutation38_livestock_registration_age.py",
            "tests/test_mutation38_livestock_registration_age_days.py",
            "tests/test_mutation38_livestock_registration_idempotency.py",
        ),
        ("app/api/animals.py", "_get_animal"): (
            "tests/test_mutation38_livestock_animal_ids.py",
            "tests/test_mutation38_livestock_legacy_ids.py",
            "tests/test_mutation38_livestock_profile_reads.py",
        ),
        ("app/api/animals.py", "list_animals"): (
            "tests/test_adversarial.py::test_create_animal_rejects_bad_enums_and_empty_tag",
        ),
        ("app/api/animals.py", "animal_profile"): (
            "tests/test_animal_profile_pagination.py::test_profile_histories_have_exact_totals_independent_pages_and_constant_queries",
            "tests/test_mutation38_livestock_profile_reads.py",
        ),
        ("app/api/animals.py", "_lock_pristine_batch_protocol_for_quarantine_reentry"): (
            "tests/test_mutation38_quarantine_overflow.py",
            "tests/test_mutation38_quarantine_cached_task.py",
            "tests/test_mutation38_quarantine_cached_batch.py",
        ),
        ("app/models/animals.py", "_derive_history_farm_id"): (
            "tests/test_mutation38_livestock_history_tenant.py",
        ),
        ("app/services/screening/s3.py", "download"): (
            "tests/test_mutation38_workflow_screening_progress.py",
        ),
        ("app/services/screening/s3.py", "delete_permanently"): (
            "tests/test_mutation38_workflow_screening_progress.py",
        ),
        ("app/services/screening/images.py", "normalize_image"): (
            "tests/test_mutation38_workflow_pixel_limits.py",
        ),
        ("app/services/screening/rotation.py", "gate_with_fallback"): (
            "tests/test_mutation38_workflow_pixel_limits.py",
        ),
    }
    selected = {selector for name in enclosing for selector in mappings.get((file, name), ())}
    module_mappings = {
        (
            "app/api/animals.py",
            "PROFILE_HISTORY_DEFAULT_LIMIT",
        ): profile_history_oracle,
        (
            "app/api/animals.py",
            "PROFILE_HISTORY_MAX_LIMIT",
        ): profile_history_oracle,
        (
            "app/services/screening/images.py",
            "MAX_DECODED_PIXELS",
        ): "tests/test_mutation38_workflow_pixel_limits.py",
    }
    for node in tree.body:
        if not (
            isinstance(node, (ast.Assign, ast.AnnAssign))
            and node.lineno <= line <= (node.end_lineno or node.lineno)
        ):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                selector = module_mappings.get((file, target.id))
                if selector:
                    selected.add(selector)
    from staged_contracts import select_staged_contracts

    selected.update(select_staged_contracts(file, tree, line))
    return sorted(selected)


def classify(file: str, name: str, line: int) -> int | None:
    def contains(*words: str) -> bool:
        return any(word in name for word in words)

    ownership = MUTDIR / "domain_ownership.json"
    if ownership.exists():
        for section in json.loads(ownership.read_text()):
            if section["file"] == file and section["start"] <= line <= section["end"]:
                return int(section["domain"])
    stem = Path(file).stem
    if file.startswith("app/simulation/"):
        direct = {
            "feed": 29,
            "market": 30,
            "finance": 31,
            "subsidy": 31,
            "montecarlo": 32,
            "shocks": 32,
            "optimization": 33,
            "planner": 34,
            "backward_planner": 34,
            "daily_ops": 35,
            "snapshot": 28,
            "vocabulary": 28,
        }
        if stem in direct:
            return direct[stem]
        if contains("feed", "fodder", "water", "grazing"):
            return 29
        if contains("market", "sale", "festival", "growth", "premium"):
            return 30
        if contains("cost", "financ", "loan", "tax", "depreciat", "subsid", "valuation"):
            return 31
        if contains("risk", "shock", "monte", "sensitivity"):
            return 32
        if contains("optimi"):
            return 33
        return 28
    if stem == "simulation_calibration":
        return 36
    if stem in {"dashboard", "owner", "summaries"}:
        return 38
    if stem == "ops_simulation" or (stem == "ops" and "/schemas/" in file):
        return 35
    if stem in {"simulation", "planner"} and "/models/" not in file:
        if contains("calibrat"):
            return 36
        if contains("snapshot", "default", "breed"):
            return 28
        if contains("plan_sales", "salestarget", "plannerin", "plannerout", "target"):
            return 34
        return 37
    if "/services/screening/" in file:
        if stem in {"raw_cleanup", "live_contract"}:
            return None
        if stem == "budget":
            return 25
        if stem == "detect":
            return 23
        if stem == "images":
            return 23 if contains("crop") else 22
        if stem in {"gate", "specialists", "rotation", "providers"}:
            return 25 if contains("budget", "admit", "retry") else 24
        if stem == "s3":
            return 21 if contains("presign_post") else 22
        if stem == "pipeline":
            if contains("healthy_control", "sample"):
                return 27
            if contains("cascade", "provider", "rotation", "gate", "specialist", "finding"):
                return 24
            if contains("detect", "crop", "coverage", "aggregate"):
                return 23
            if contains(
                "derivative", "normaliz", "content", "object", "image_bytes", "process_image"
            ):
                return 22
            return 25
    if stem == "screening":
        if contains("review"):
            return 26
        if contains("stat", "wilson", "export", "dataset", "precision", "evaluation"):
            return 27
        if contains("batch", "upload", "capture"):
            return 21
        if contains("crop", "box"):
            return 23
        return 22
    if "/services/notifications/" in file:
        if stem in {"outbox", "providers"}:
            return None
        return 20
    if stem == "cadence":
        return 19
    if stem == "health_rounds":
        return 10
    if stem == "health":
        return 11 if contains("restriction", "withdrawal", "clearance", "hold") else 9
    if stem == "breeding":
        return (
            4
            if contains(
                "breeding_candidates",
                "candidate",
                "create_breeding",
                "record_breeding",
                "eligible",
                "ready",
                "kin",
                "related",
                "lineage",
                "sire",
                "buck",
            )
            else 5
        )
    if stem == "kidding":
        return 6
    if stem == "purchases":
        return 3
    if stem == "animals":
        if contains("status", "sale", "death", "cull", "dispose"):
            return 8
        if contains("move", "bucket", "transition", "reclass"):
            return 2
        return 1
    if stem == "buckets" or stem == "lifecycle":
        return 2
    if stem == "species":
        return 4
    if stem in {"feeding", "feed_rules"}:
        return (
            13
            if contains(
                "dispens",
                "mix",
                "stock",
                "inventory",
                "ingredient",
                "quantity",
                "gram",
                "recipein",
                "recipeout",
            )
            else 12
        )
    if stem == "finance":
        if contains("insurance", "policy", "premium", "claim", "renew"):
            return 15
        if contains("pnl", "memo", "stock_value", "summary"):
            return 16
        return 14
    if stem == "tasks":
        if contains("quarantine") or (name == "complete_task" and 549 <= line <= 560):
            return 3
        if contains("weaning", "litter") or (
            name == "complete_task"
            and (464 <= line <= 541 or 568 <= line <= 586 or 622 <= line <= 658)
        ):
            return 7
        if contains("movement", "linked_breeding"):
            return 2
        if contains("spawn", "recurr", "assign", "scope", "pending_manual", "capacity"):
            return 18
        return 17
    if stem == "helpers":
        return 1
    if stem == "constants":
        return 4
    if stem == "utils" and contains("money"):
        return 14
    return None


def semantic_mutant(spec: dict[str, Any]) -> dict[str, Any]:
    path = BACKEND / spec["file"]
    source = path.read_text()
    original, replacement = spec["original"], spec["replacement"]
    if source.count(original) != 1:
        raise ValueError(f"semantic snippet is not unique: {spec['file']} {spec['detail']}")
    start = source.index(original)
    line = source.count("\n", 0, start) + 1
    end = source.count("\n", 0, start + len(original)) + 1
    tree = ast.parse(source)
    parents = mutate_gen.build_parent_map(tree)
    statements = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.stmt)
        and isinstance(parents.get(id(n)), mutate_gen.STMT_PARENTS)
        and n.lineno <= line
        and (n.end_lineno or n.lineno) >= end
    ]
    if not statements:
        raise ValueError(f"no enclosing statement for {spec['detail']}")
    stmt = min(statements, key=lambda n: (n.end_lineno or n.lineno) - n.lineno)
    lines = source.splitlines(keepends=True)
    span = "".join(lines[mutate_gen.statement_start(stmt) - 1 : stmt.end_lineno])
    if span.count(original) != 1:
        raise ValueError(f"snippet crosses statements: {spec['detail']}")
    edited = span.replace(original, replacement, 1)
    import textwrap

    edited_stmt = textwrap.dedent(edited).rstrip()
    compile(mutate_gen.splice(source, stmt, edited_stmt), str(path), "exec")
    depth = 0
    probe = parents.get(id(stmt))
    while probe is not None:
        depth += isinstance(probe, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
        probe = parents.get(id(probe))
    return {
        "id": hashlib.sha256(digest_json(spec).encode()).hexdigest()[:16],
        "file": spec["file"],
        "tier": 1,
        "scope": "function" if depth else "module",
        "line": line,
        "end_line": end,
        "kind": "semantic",
        "detail": spec["detail"],
        "stmt_line": mutate_gen.statement_start(stmt),
        "stmt_end_line": stmt.end_lineno,
        "stmt_col": stmt.col_offset,
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "orig_span": span,
        "orig_stmt": ast.unparse(stmt),
        "mut_stmt": edited_stmt,
        "domain": spec["domain"],
        "oracles": spec.get("tests", []),
    }


def generate(spec_paths: list[str]) -> None:
    original_targets = mutate_gen.target_files
    pure_models = ("species", "lifecycle", "feed_rules")
    method_models = ("animals", "tasks", "screening")
    extra = [BACKEND / f"app/models/{name}.py" for name in pure_models + method_models]
    mutate_gen.target_files = lambda: sorted(set(original_targets() + extra))
    try:
        mutate_gen.generate()
    finally:
        mutate_gen.target_files = original_targets
    mutants = json.loads((MUTDIR / "manifest.json").read_text())
    trees = {file: ast.parse((BACKEND / file).read_text()) for file in {m["file"] for m in mutants}}
    selected = []
    excluded: Counter[str] = Counter()
    for m in mutants:
        # Declarative DDL is exercised by reviewed SQL constraints instead.
        if (
            Path(m["file"]).stem in method_models
            and m["file"].startswith("app/models/")
            and m["scope"] != "function"
        ):
            continue
        domain = classify(m["file"], symbol(trees[m["file"]], m["line"]), m["line"])
        if domain:
            selected.append({**m, "domain": domain})
        else:
            excluded[m["file"]] += 1
    for name in spec_paths:
        for spec in json.loads(Path(name).read_text()):
            selected.append(semantic_mutant(spec))
    for m in selected:
        name = symbol(
            trees[m["file"]]
            if m["file"] in trees
            else ast.parse((BACKEND / m["file"]).read_text()),
            m["line"],
        )
        progress = {
            (
                "app/simulation/optimization.py",
                "_sample_grid",
            ): "test_optimizer_grid_completes_with_budget_and_all_dimensions",
            (
                "app/simulation/daily_ops.py",
                "_kidding",
            ): "test_colliding_newborn_tags_complete_without_losing_existing_animals",
            (
                "app/simulation/planner.py",
                "_purchases_from",
            ): "test_purchase_chunking_completes_and_conserves_requested_head",
            **{
                (
                    "app/simulation/finance.py",
                    finance_symbol,
                ): "test_irr_isolation_completes_and_reports_all_sparse_dated_returns"
                for finance_symbol in (
                    "_normalise_power_terms",
                    "_decimal_normalise_power_terms",
                    "_decimal_normalise_terms",
                    "_all_decimal_power_roots",
                    "_positive_power_roots",
                    "_decimal_power_sum",
                    "_decimal_sign",
                    "_decimal_sign_variations",
                    "_exact_decimal_power_roots",
                    "irr_roots",
                    "assess_irr",
                )
            },
            (
                "app/services/simulation_calibration.py",
                "_isotonic_fit",
            ): "test_measured_growth_smoothing_completes_and_preserves_pooled_observations",
            (
                "app/services/simulation_calibration.py",
                "_curve_from_observations",
            ): "test_measured_growth_smoothing_completes_and_preserves_pooled_observations",
        }.get((m["file"], name))
        if progress:
            m["oracles"] = sorted(
                {
                    *m.get("oracles", []),
                    f"tests/test_000_mutation38_simulation_progress.py::{progress}",
                }
            )
        tree = trees.get(m["file"])
        if tree is None:
            tree = ast.parse((BACKEND / m["file"]).read_text())
        contracts = contract_oracles(m["file"], tree, m["line"])
        if contracts:
            m["oracles"] = sorted({*m.get("oracles", []), *contracts})
    selected.sort(
        key=lambda m: (m["domain"], m["kind"] != "semantic", m["file"], m["line"], m["id"])
    )
    atomic_json(MUTDIR / "manifest.json", selected)
    counts = Counter(m["domain"] for m in selected)
    plan = {
        "schema": 1,
        "selection_policy": (
            "all measured coverers; complete declared domain suite for import-time/untraced sites"
        ),
        "total": len(selected),
        "campaigns": [
            {"id": i, "name": name, "mutants": counts[i], "suite": suite(i)}
            for i, name in enumerate(NAMES, 1)
        ],
        "outside_business_scope": dict(excluded),
    }
    atomic_json(MUTDIR / "domain-plan.json", plan)
    print(json.dumps(plan, indent=2))


class DomainRunner(Runner):
    def __init__(self, workers: int, max_seconds: float | None, *, phase_timeout: float = 600):
        super().__init__(workers, max_seconds, phase_timeout=phase_timeout)
        self.identity["config"]["domain_selection"] = "all-coverers-or-declared-domain-suite-v1"
        self.campaign_id = digest_json(self.identity)
        self.done_ids = {
            mid
            for mid, record in latest_compatible(
                read_results(self.results_path), self.campaign_id, self.manifest
            ).items()
            if record.get("status") in {"KILLED", "SURVIVED", "INVALID"}
        }

    def select_tests(self, m: dict[str, Any]) -> tuple[list[str], bool]:
        selection = sorted(self.ctx.get(m["file"], {}).get(m["line"], set()))
        if m.get("scope") == "module" or not selection:
            selection = suite(m["domain"])
        oracles = [
            test for test in m.get("oracles", []) if (BACKEND / test.split("::")[0]).exists()
        ]
        # Bounded progress oracles must run before direct calls that could
        # hang. The exact clean baseline uses this same ordered selection.
        return list(dict.fromkeys(sorted(oracles) + sorted(selection))), False


def summarize() -> None:
    manifest = {m["id"]: m for m in json.loads((MUTDIR / "manifest.json").read_text())}
    all_records = read_results(MUTDIR / "results.jsonl")
    inputs = input_identity(BACKEND)
    artifacts = {
        "manifest_sha256": sha_file(MUTDIR / "manifest.json"),
        "coverage_sha256": sha_file(BACKEND / ".coverage-mut"),
        "coverage_provenance_sha256": sha_file(BACKEND / ".coverage-mut.provenance.json"),
    }
    records = [
        record
        for record in all_records
        if record.get("provenance")
        and record.get("campaign_id") == digest_json(record["provenance"])
        and all(record["provenance"].get(key) == value for key, value in inputs.items())
        and all(record["provenance"].get(key) == value for key, value in artifacts.items())
    ]
    campaign_ids = list(
        dict.fromkeys(str(r["campaign_id"]) for r in records if r.get("campaign_id"))
    )
    campaign = campaign_ids[-1] if campaign_ids else "unmeasured"
    latest = latest_compatible(records, campaign, manifest)
    rows = []
    for domain, name in enumerate(NAMES, 1):
        targets = {mid for mid, m in manifest.items() if m["domain"] == domain}
        statuses = Counter(latest[mid]["status"] for mid in targets if mid in latest)
        rows.append(
            {
                "id": domain,
                "name": name,
                "targets": len(targets),
                "missing": len(targets - latest.keys()),
                "statuses": dict(statuses),
            }
        )
    report = {
        "campaign_id": campaign,
        "total": len(manifest),
        "attempted": len(latest),
        "statuses": dict(Counter(r["status"] for r in latest.values())),
        "campaigns": rows,
        "incompatible_historical_attempts": len(all_records) - len(records),
    }
    atomic_json(MUTDIR / "domain-report.json", report)
    print(json.dumps(report, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["generate", "run", "report"])
    parser.add_argument("--specs", nargs="*", default=[str(MUTDIR / "domain_specs.json")])
    parser.add_argument("--domains", default=",".join(str(i) for i in range(1, 39)))
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--status", nargs="*", default=[])
    parser.add_argument("--semantic-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.action == "generate":
        generate(args.specs)
    elif args.action == "report":
        summarize()
    else:
        domains = {int(i) for i in args.domains.split(",")}
        if args.dry_run:
            manifest = json.loads((MUTDIR / "manifest.json").read_text())
            print(
                json.dumps(
                    {
                        "targets": [
                            m["id"]
                            for m in manifest
                            if m["domain"] in domains
                            and (not args.semantic_only or m["kind"] == "semantic")
                        ],
                        "writes": False,
                    }
                )
            )
            return
        os.environ["MUTATE_FULL_PHASE"] = "1"
        runner = DomainRunner(args.workers, None, phase_timeout=args.timeout)
        try:
            latest = latest_compatible(
                read_results(runner.results_path), runner.campaign_id, runner.manifest
            )
            todo = [
                m
                for m in runner.manifest.values()
                if m["domain"] in domains
                and (not args.semantic_only or m["kind"] == "semantic")
                and (
                    (args.status and latest.get(m["id"], {}).get("status") in args.status)
                    or (not args.status and m["id"] not in runner.done_ids)
                )
            ]
            # Exercise reviewed business changes across all 38 areas first,
            # then execute every generic target with the same captured inputs.
            todo.sort(key=lambda m: (m["kind"] != "semantic", m["domain"]))
            print(f"Campaign {runner.campaign_id}: {len(todo)} targets", flush=True)
            runner.run(todo)
        finally:
            runner.close()


if __name__ == "__main__":
    main()
