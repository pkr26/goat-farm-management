"""Independent streaming verification of NEW complete generic mutation receipts.

Old sampled/module-fallback policies are intentionally not certified. Exact test
selection is recomputed from captured coverage and its passing concrete inventory;
no Runner is constructed and no test or mutation process is started.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND / "mutation"))
import mutate_identity  # noqa: E402
import mutate_run  # noqa: E402
import mutate_selection  # noqa: E402
import verify_domain_receipts as shared  # noqa: E402
from mutate_identity import (  # noqa: E402
    EXECUTION_MODE_POLICY,
    database_admin_sha256,
    digest_json,
    execution_modes,
    input_identity,
    sha_bytes,
    sha_file,
)

EvidenceError = shared.EvidenceError


def assert_context_unchanged(context: shared.Context) -> None:
    if (
        input_identity(BACKEND) != context.inputs
        or digest_json(execution_modes(BACKEND, context.inputs)) != context.execution_modes_sha256
        or any(
            sha_file(path) != context.artifacts[key]
            for key, path in (
                ("manifest_sha256", BACKEND / "mutation/manifest.json"),
                ("coverage_sha256", BACKEND / ".coverage-mut"),
                ("coverage_provenance_sha256", BACKEND / ".coverage-mut.provenance.json"),
            )
        )
    ):
        raise EvidenceError("frozen generic inputs/artifacts changed during verification")


def capture_context(campaign: str) -> shared.Context:
    inputs = input_identity(BACKEND)
    modes_sha256 = digest_json(execution_modes(BACKEND, inputs))
    manifest_bytes = (BACKEND / "mutation/manifest.json").read_bytes()
    coverage_bytes = (BACKEND / ".coverage-mut").read_bytes()
    provenance_bytes = (BACKEND / ".coverage-mut.provenance.json").read_bytes()
    coverage = json.loads(provenance_bytes)
    database_hash = database_admin_sha256()
    if (
        coverage.get("inputs") != inputs
        or coverage.get("coverage_sha256") != sha_bytes(coverage_bytes)
        or coverage.get("baseline_exit_code") != 0
        or coverage.get("test_database_admin_sha256") != database_hash
        or coverage.get("execution_mode_policy") != EXECUTION_MODE_POLICY
        or coverage.get("execution_modes_sha256") != modes_sha256
    ):
        raise EvidenceError("generic coverage is not bound to current passing inputs/database")
    mutants = json.loads(manifest_bytes)
    manifest = {mutant["id"]: mutant for mutant in mutants}
    if len(manifest) != len(mutants) or not manifest:
        raise EvidenceError("generic manifest is empty or contains duplicate IDs")
    contexts = mutate_run.load_coverage_contexts(coverage_bytes, coverage)
    selector = mutate_selection.GenericSelection(contexts, coverage, backend=BACKEND)
    selector.inventory()  # Old maps lacking a complete passing inventory fail closed.

    def check_provenance(provenance: dict[str, Any]) -> None:
        config = provenance.get("config")
        if (
            provenance.get("schema") != 2
            or not isinstance(config, dict)
            or config.get("full") is not True
            or config.get("generic_selection") != selector.configuration()
            or config.get("isolation_policy") != mutate_run.ISOLATION_POLICY
            or config.get("database_cleanup") != mutate_run.database_cleanup_configuration()
            or config.get("domain_selection") is not None
            or config.get("bootstrap") is not None
            or config.get("test_database_admin_sha256") != database_hash
            or type(config.get("workers")) is not int
            or config["workers"] <= 0
            or config.get("sample_cap") != mutate_run.SAMPLE_CAP
            or config.get("pytest_addopts") != "cleared"
            or config.get("execution_mode_policy") != EXECUTION_MODE_POLICY
            or config.get("execution_modes_sha256") != modes_sha256
        ):
            raise EvidenceError("not the new complete generic selection/isolation policy")
        try:
            timeout = mutate_selection.SelectionTimeoutPolicy(
                coverage, explicit_seconds=config.get("phase_timeout")
            )
        except (ValueError, TypeError) as exc:
            raise EvidenceError("invalid generic timeout policy") from exc
        if config.get("timeout_policy") != timeout.configuration():
            raise EvidenceError("generic timeout configuration is not the captured policy")

    def check_receipt_policy(record: dict[str, Any]) -> None:
        check_provenance(record["provenance"])
        try:
            timeout = mutate_selection.SelectionTimeoutPolicy(
                coverage, explicit_seconds=record["provenance"]["config"].get("phase_timeout")
            ).seconds(record["selection"])
        except (ValueError, TypeError, KeyError) as exc:
            raise EvidenceError("selection cannot establish the captured generic timeout") from exc
        applied = (record.get("timeout_seconds"), record["baseline"].get("timeout_seconds"))
        if any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            or value != timeout
            for value in applied
        ):
            raise EvidenceError("clean and mutant phases did not record the same captured timeout")
        if any(
            phase.get(key) != modes_sha256
            for phase in (record, record["baseline"])
            for key in ("execution_modes_before_sha256", "execution_modes_after_sha256")
        ):
            raise EvidenceError("clean or mutant execution permissions differ from captured source")

    context = shared.Context(
        campaign,
        manifest,
        inputs,
        {
            "manifest_sha256": sha_bytes(manifest_bytes),
            "coverage_sha256": sha_bytes(coverage_bytes),
            "coverage_provenance_sha256": sha_bytes(provenance_bytes),
        },
        database_hash,
        selector.select,
        source_bytes=lambda relative: (BACKEND / relative).read_bytes(),
        profile="generic",
        policy_check=check_receipt_policy,
        provenance_check=check_provenance,
        execution_modes_sha256=modes_sha256,
    )
    assert_context_unchanged(context)
    return context


def verify(paths: list[Path], context: shared.Context) -> tuple[dict[str, Any], dict[str, Any]]:
    if context.profile != "generic":
        raise EvidenceError("generic verifier requires a generic context")
    report, proof = shared.stream_verify(paths, context)
    assert_context_unchanged(context)
    proof["generic_consumer_sha256"] = sha_file(Path(__file__))
    proof["generic_selection_policy_sha256"] = context.inputs.get("harness", {}).get(
        "mutate_selection.py"
    )
    proof["execution_mode_policy"] = EXECUTION_MODE_POLICY
    proof["execution_modes_sha256"] = context.execution_modes_sha256
    return report, proof


def assert_policy_modules(context: shared.Context) -> None:
    for module in (mutate_run, mutate_selection, mutate_identity, shared):
        if not isinstance(module.__file__, str):
            raise EvidenceError("imported generic policy has no concrete source file")
        path = Path(module.__file__).resolve()
        if path != (BACKEND / "mutation" / path.name).resolve() or sha_file(
            path
        ) != context.inputs.get("harness", {}).get(path.name):
            raise EvidenceError(
                "imported generic producer/consumer policy differs from frozen source"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", required=True)
    parser.add_argument(
        "--results",
        type=Path,
        action="append",
        help="chronological receipt streams; repeated paths are folded in supplied order",
    )
    parser.add_argument(
        "--report", type=Path, default=BACKEND / "mutation/campaigns/generic-report.json"
    )
    parser.add_argument(
        "--verification",
        type=Path,
        default=BACKEND / "mutation/campaigns/generic-verification.json",
    )
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--require-measured", action="store_true")
    args = parser.parse_args()
    context = capture_context(args.campaign)
    assert_policy_modules(context)
    report, proof = verify(args.results or [BACKEND / "mutation/results.jsonl"], context)
    for path, value in ((args.report, report), (args.verification, proof)):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(value, indent=2) + "\n")
        temporary.replace(path)
    print(
        json.dumps(
            {
                "campaign_id": context.campaign,
                "attempted": report["attempted"],
                "statuses": report["statuses"],
                "verified_complete_latest_verdicts": proof["verified_complete_latest_verdicts"],
                "missing_targets": proof["missing_targets"],
                "error_count": proof["error_count"],
            },
            indent=2,
        )
    )
    return int(
        proof["error_count"] != 0
        or (args.require_complete and proof["missing_targets"] != 0)
        or (args.require_measured and not proof["all_target_verdicts_verified"])
    )


if __name__ == "__main__":
    raise SystemExit(main())
