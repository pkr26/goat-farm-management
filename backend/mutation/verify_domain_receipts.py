"""Streaming verification of complete domain selections and original pytest receipts.

This independent consumer neither changes nor replaces official evidence.
It imports the frozen producer's classifier and identity helpers. Whole-file and
family selections are expanded against their exact passing baseline inventory.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND / "mutation"))
import mutate_domains  # noqa: E402
import mutate_run  # noqa: E402
from mutate_identity import (  # noqa: E402
    database_admin_sha256,
    digest_json,
    input_identity,
    sha_bytes,
    sha_file,
)


class EvidenceError(ValueError):
    """A purported definitive verdict lacks its complete domain evidence."""


@dataclass
class Context:
    campaign: str
    manifest: dict[str, dict[str, Any]]
    inputs: dict[str, Any]
    artifacts: dict[str, Any]
    database_hash: str
    expected_selection: Callable[[dict[str, Any]], list[str]]
    source_bytes: Callable[[str], bytes] | None = None


def capture_context(campaign: str) -> Context:
    """Hash and parse the same captured artifact bytes, without runner startup."""
    inputs = input_identity(BACKEND)
    manifest_bytes = (BACKEND / "mutation/manifest.json").read_bytes()
    coverage_bytes = (BACKEND / ".coverage-mut").read_bytes()
    provenance_bytes = (BACKEND / ".coverage-mut.provenance.json").read_bytes()
    coverage_provenance = json.loads(provenance_bytes)
    database_hash = database_admin_sha256()
    if (
        coverage_provenance.get("inputs") != inputs
        or coverage_provenance.get("coverage_sha256") != sha_bytes(coverage_bytes)
        or coverage_provenance.get("baseline_exit_code") != 0
        or coverage_provenance.get("test_database_admin_sha256") != database_hash
    ):
        raise EvidenceError("captured coverage is not bound to current clean inputs/database")
    mutants = json.loads(manifest_bytes)
    manifest = {mutant["id"]: mutant for mutant in mutants}
    if len(manifest) != len(mutants):
        raise EvidenceError("captured manifest has duplicate IDs")
    selector = object.__new__(mutate_domains.DomainRunner)
    selector.ctx = mutate_run.load_coverage_contexts(coverage_bytes, coverage_provenance)

    def expected(mutant: dict[str, Any]) -> list[str]:
        selection, _ = selector.select_tests(mutant)
        return list(selection)

    context = Context(
        campaign,
        manifest,
        inputs,
        {
            "manifest_sha256": sha_bytes(manifest_bytes),
            "coverage_sha256": sha_bytes(coverage_bytes),
            "coverage_provenance_sha256": sha_bytes(provenance_bytes),
        },
        database_hash,
        expected,
        source_bytes=lambda relative: (BACKEND / relative).read_bytes(),
    )
    assert_context_unchanged(context)
    return context


def assert_context_unchanged(context: Context) -> None:
    if input_identity(BACKEND) != context.inputs or any(
        sha_file(path) != context.artifacts[key]
        for key, path in (
            ("manifest_sha256", BACKEND / "mutation/manifest.json"),
            ("coverage_sha256", BACKEND / ".coverage-mut"),
            ("coverage_provenance_sha256", BACKEND / ".coverage-mut.provenance.json"),
        )
    ):
        raise EvidenceError("frozen inputs/artifacts changed during verification")


def ancestors(nodeid: str) -> set[str]:
    """Exactly match file, class, function, parameter family, or concrete case."""
    parts = nodeid.split("[", 1)[0].split("::")
    return {nodeid, *("::".join(parts[:index]) for index in range(1, len(parts) + 1))}


def inventory(receipt: Any, *, clean: bool) -> tuple[set[str], dict[str, dict[str, Any]]]:
    if not isinstance(receipt, dict):
        raise EvidenceError("missing structured pytest receipt")
    if (
        type(receipt.get("exit_code")) is not int
        or receipt.get("collection_errors") != []
        or type(receipt.get("collected")) is not int
        or receipt["collected"] <= 0
    ):
        raise EvidenceError("invalid exit, collection errors, or empty collection")
    reports = receipt.get("reports")
    if not isinstance(reports, list) or not reports:
        raise EvidenceError("missing pytest reports")
    phases: dict[str, dict[str, Any]] = {}
    for row in reports:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("nodeid"), str)
            or not row["nodeid"]
            or row.get("when") not in ("setup", "call", "teardown")
            or row.get("outcome") not in ("passed", "failed", "skipped")
            or type(row.get("assertion")) is not bool
        ):
            raise EvidenceError("malformed pytest report")
        node = phases.setdefault(row["nodeid"], {})
        if row["when"] in node:
            raise EvidenceError("duplicate node/phase report")
        node[row["when"]] = row
        if row["assertion"] and (row["when"] != "call" or row["outcome"] != "failed"):
            raise EvidenceError("assertion marker outside a failed test call")
    for nodeid, node in phases.items():
        setup, call, teardown = (node.get(phase) for phase in ("setup", "call", "teardown"))
        if not setup or not teardown or teardown["outcome"] != "passed":
            raise EvidenceError(f"incomplete setup/teardown for {nodeid}")
        if setup["outcome"] == "skipped":
            if call is not None:
                raise EvidenceError("setup skip must have no test-call report")
        elif setup["outcome"] == "passed":
            if call is None or (clean and call["outcome"] not in ("passed", "skipped")):
                raise EvidenceError("missing or failing baseline test call")
        else:
            raise EvidenceError("setup failure is not assertion-kill evidence")
    nodes = set(phases)
    if clean and (receipt["collected"] != len(nodes) or receipt["exit_code"] != 0):
        raise EvidenceError("baseline did not finish every collected concrete node")
    if not clean and len(nodes) > receipt["collected"]:
        raise EvidenceError("more touched nodes than collected tests")
    return nodes, phases


def verify_definitive(record: dict[str, Any], context: Context) -> dict[str, Any]:
    """Validate an observed KILLED/SURVIVED label without changing its semantics."""
    import bootstrap_policy

    if record.get("status_policy") == bootstrap_policy.POLICY:
        try:
            return bootstrap_policy.verify(record, context)
        except (KeyError, TypeError, AttributeError) as exc:
            raise EvidenceError(f"malformed bootstrap evidence: {exc}") from exc
    if record.get("provenance", {}).get("config", {}).get("bootstrap") is not None:
        raise EvidenceError("bootstrap campaign cannot use legacy single-phase evidence")
    selection = record.get("selection")
    baseline = record.get("baseline")
    config = record["provenance"].get("config", {})
    if (
        record["provenance"].get("schema") != 2
        or record.get("status_policy") != "assertions-only-timeouts-inconclusive"
        or record.get("selection_mode") != "complete"
        or config.get("full") is not True
        or config.get("domain_selection") != "all-coverers-or-declared-domain-suite-v1"
        or config.get("test_database_admin_sha256") != context.database_hash
    ):
        raise EvidenceError("not the current complete domain/assertions-only policy")
    if (
        not isinstance(selection, list)
        or not selection
        or any(not isinstance(node, str) or not node for node in selection)
        or len(set(selection)) != len(selection)
        or type(record.get("n_tests")) is not int
        or record["n_tests"] != len(selection)
        or not isinstance(baseline, dict)
        or baseline.get("status") != "pass"
    ):
        raise EvidenceError("invalid complete selection or missing passing baseline")
    digest = digest_json(selection)
    if not (record.get("selection_sha256") == digest == baseline.get("selection_sha256")):
        raise EvidenceError("baseline and mutant exact ordered selection hashes disagree")
    baseline_receipt = baseline.get("pytest_receipt")
    expected_selection = context.expected_selection(context.manifest[record["id"]])
    if selection != expected_selection:
        raise EvidenceError("selection omits or reorders independently recomputed domain coverers")
    if not isinstance(baseline_receipt, dict):
        raise EvidenceError("missing baseline structured pytest receipt")
    baseline_nodes, _ = inventory(baseline_receipt, clean=True)
    if mutate_run.classify_pytest(baseline_receipt["exit_code"], baseline_receipt) != "pass":
        raise EvidenceError("producer policy does not classify the baseline as passing")
    selectors = set(selection)
    hit: set[str] = set()
    for nodeid in baseline_nodes:
        matching = selectors & ancestors(nodeid)
        if not matching:
            raise EvidenceError("baseline concrete node lies outside the recorded selection")
        hit.update(matching)
    if hit != selectors:
        raise EvidenceError("at least one selector covers no baseline concrete node")
    mutant_receipt = record.get("pytest_receipt")
    if not isinstance(mutant_receipt, dict):
        raise EvidenceError("missing mutant structured pytest receipt")
    mutant_nodes, _ = inventory(mutant_receipt, clean=False)
    if mutant_receipt["collected"] != baseline_receipt["collected"]:
        raise EvidenceError("mutant and baseline collection counts disagree")
    if not mutant_nodes.issubset(baseline_nodes):
        raise EvidenceError("mutant touched node is absent from the clean baseline inventory")
    verdict = mutate_run.classify_pytest(mutant_receipt["exit_code"], mutant_receipt)
    expected = {"KILLED": "kill", "SURVIVED": "pass"}[record["status"]]
    if verdict != expected:
        raise EvidenceError("declared verdict contradicts the frozen producer classifier")
    if record["status"] == "SURVIVED" and mutant_nodes != baseline_nodes:
        raise EvidenceError("survival receipt omits baseline concrete nodes")
    return {
        "concrete_baseline_nodes": len(baseline_nodes),
        "concrete_touched_nodes": len(mutant_nodes),
        "selector_count": len(selection),
    }


def stream_verify(path: Path, context: Context) -> tuple[dict[str, Any], dict[str, Any]]:
    """Retain only compact latest results; provenance cache is limited to four."""
    provenance_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
    latest: dict[str, dict[str, Any]] = {}
    compatible = total = errors_count = 0
    errors: list[str] = []

    def error(message: str) -> None:
        nonlocal errors_count
        errors_count += 1
        if len(errors) < 100:
            errors.append(message)

    with path.open() as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                error(f"line {number}: malformed JSONL evidence")
                continue
            if (
                not isinstance(record, dict)
                or not isinstance(record.get("id"), str)
                or not isinstance(record.get("status"), str)
            ):
                error(f"line {number}: invalid attempt object")
                continue
            total += 1
            provenance = record.get("provenance")
            campaign = record.get("campaign_id")
            if not isinstance(provenance, dict) or not isinstance(campaign, str):
                if campaign == context.campaign:
                    mutant = context.manifest.get(record["id"])
                    if mutant is None or record.get("mutant_digest") != digest_json(mutant):
                        error(
                            f"line {number}: unproven current attempt has invalid mutant identity"
                        )
                        continue
                    latest[record["id"]] = {
                        "id": record["id"],
                        "status": record["status"],
                        "verified": False,
                        "error": "current attempt has no provenance; never definitive evidence",
                    }
                    if record["status"] != "INFRA_ERROR":
                        error(
                            f"line {number}: unproven current attempt claims "
                            "a non-infrastructure verdict"
                        )
                continue
            cached = provenance_cache.get(campaign)
            if cached != provenance:
                valid = (
                    campaign == digest_json(provenance)
                    and all(provenance.get(key) == value for key, value in context.inputs.items())
                    and all(
                        provenance.get(key) == value for key, value in context.artifacts.items()
                    )
                )
                if not valid:
                    if campaign == context.campaign:
                        error(f"line {number}: current campaign provenance is inconsistent")
                        mutant = context.manifest.get(record["id"])
                        if mutant is not None and record.get("mutant_digest") == digest_json(
                            mutant
                        ):
                            latest[record["id"]] = {
                                "id": record["id"],
                                "status": record["status"],
                                "verified": False,
                                "error": "current provenance is inconsistent",
                            }
                    continue
                provenance_cache[campaign] = provenance
                provenance_cache.move_to_end(campaign)
                if len(provenance_cache) > 4:
                    provenance_cache.popitem(last=False)
            compatible += 1
            if campaign != context.campaign:
                continue
            mutant = context.manifest.get(record["id"])
            if mutant is None or record.get("mutant_digest") != digest_json(mutant):
                error(f"line {number}: unknown or changed manifest mutant")
                continue
            if any(
                record.get(key) != mutant.get(key)
                for key in (
                    "file",
                    "line",
                    "kind",
                    "detail",
                    "tier",
                )
            ):
                error(f"line {number}: mutant metadata differs from its manifest")
                continue
            row: dict[str, Any] = {
                "id": record["id"],
                "status": record["status"],
                "verified": False,
            }
            if record["status"] in ("KILLED", "SURVIVED"):
                try:
                    row.update(verify_definitive(record, context))
                    row["verified"] = True
                except EvidenceError as exc:
                    row["error"] = str(exc)
                    error(f"line {number}, {record['id']}: {exc}")
            elif record["status"] not in (
                "INVALID",
                "NOT_COVERED",
                "INFRA_ERROR",
                "INCONCLUSIVE_TIMEOUT",
            ):
                error(f"line {number}: unknown status {record['status']!r}")
            latest[record["id"]] = row
    rows = []
    for domain, name in enumerate(mutate_domains.NAMES, 1):
        targets = {mid for mid, mutant in context.manifest.items() if mutant["domain"] == domain}
        rows.append(
            {
                "id": domain,
                "name": name,
                "targets": len(targets),
                "missing": len(targets - latest.keys()),
                "statuses": dict(
                    Counter(latest[mid]["status"] for mid in targets if mid in latest)
                ),
            }
        )
    report = {
        "campaign_id": context.campaign,
        "total": len(context.manifest),
        "attempted": len(latest),
        "statuses": dict(Counter(row["status"] for row in latest.values())),
        "campaigns": rows,
        "incompatible_historical_attempts": total - compatible,
    }
    definitive = [row for row in latest.values() if row["verified"]]
    killed = sum(row["status"] == "KILLED" for row in definitive)
    verification = {
        "scope": "Independent streaming validation of unchanged official domain receipts.",
        "campaign_id": context.campaign,
        "consumer_sha256": sha_file(Path(__file__)),
        "producer_policy_sha256": context.inputs.get("harness", {}).get("mutate_run.py"),
        "attempt_records": total,
        "compatible_records": compatible,
        "verified_complete_latest_verdicts": len(definitive),
        "verified_assertion_kills": killed,
        "verified_survivors": len(definitive) - killed,
        "unmeasured_latest_statuses": dict(
            Counter(row["status"] for row in latest.values() if not row["verified"])
        ),
        "missing_targets": len(context.manifest) - len(latest),
        "error_count": errors_count,
        "errors_first_100": errors,
        "all_target_verdicts_verified": len(definitive) == len(context.manifest)
        and errors_count == 0,
        "raw_latest_rows": list(latest.values()),
        "missing_provenance_latest_ids": [
            row["id"]
            for row in latest.values()
            if row.get("error", "").startswith("current attempt has no provenance")
        ],
        "independent_selection_policy": (
            "Exact ordered DomainRunner.select_tests against captured, hashed "
            "coverage/provenance bytes; no Runner initialization."
        ),
        "limitation": (
            "The original producer records total collection count and executed node IDs, "
            "not the unexecuted mutant node inventory after -x. For early kills this validates "
            "equal collection count and every touched ID against the complete clean baseline; "
            "it does not claim to reconstruct unseen IDs."
        ),
    }
    return report, verification


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, default=BACKEND / "mutation/results.jsonl")
    parser.add_argument("--campaign")
    parser.add_argument(
        "--report",
        type=Path,
        default=BACKEND / "mutation/domain-report.json",
    )
    parser.add_argument(
        "--verification",
        type=Path,
        default=BACKEND / "mutation/domain-verification.json",
    )
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--require-measured", action="store_true")
    args = parser.parse_args()
    campaign = args.campaign
    if campaign is None:
        match = re.search(
            r"Campaign ([a-f0-9]{64})",
            (BACKEND / "mutation/domain-full.log").read_text(),
        )
        if match is None:
            parser.error("supply the authoritative campaign ID with --campaign")
        campaign = match.group(1)
    context = capture_context(campaign)
    for module, name in (
        (mutate_domains, "mutate_domains.py"),
        (mutate_run, "mutate_run.py"),
        (sys.modules["mutate_identity"], "mutate_identity.py"),
    ):
        module_file = module.__file__
        if not isinstance(module_file, str):
            raise EvidenceError("producer policy has no concrete source file")
        policy = Path(module_file).resolve()
        if (
            policy != (BACKEND / "mutation" / name).resolve()
            or sha_file(policy) != context.inputs["harness"][name]
        ):
            raise EvidenceError("imported producer policy does not match the frozen source")
    report, verification = stream_verify(args.results, context)
    assert_context_unchanged(context)
    for path, value in ((args.report, report), (args.verification, verification)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2) + "\n")
    print(
        json.dumps(
            {
                "campaign_id": campaign,
                "attempted": report["attempted"],
                "statuses": report["statuses"],
                "verified_complete_latest_verdicts": verification[
                    "verified_complete_latest_verdicts"
                ],
                "error_count": verification["error_count"],
                "missing_targets": verification["missing_targets"],
            },
            indent=2,
        )
    )
    return int(
        verification["error_count"] != 0
        or (args.require_complete and verification["missing_targets"] != 0)
        or (args.require_measured and not verification["all_target_verdicts_verified"])
    )


if __name__ == "__main__":
    raise SystemExit(main())
