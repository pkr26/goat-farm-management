"""Separate bootstrap and native evidence; never invent native mutant execution."""

from __future__ import annotations

import copy
import hashlib
import textwrap
from typing import Any

from mutate_identity import digest_json

POLICY = "bootstrap-first-assertions-only-timeouts-inconclusive-v1"
SCRIPT = "tests/bootstrap/test_application_bootstrap.py"
SELECTION = [SCRIPT + "::test_backend_constructs_and_serves_the_public_health_app"]
CONFCUTDIR = "tests/bootstrap"


def configuration(inputs: dict[str, Any]) -> dict[str, Any]:
    script_sha = inputs.get("tests", {}).get(SCRIPT)
    if not isinstance(script_sha, str) or len(script_sha) != 64:
        raise ValueError("bootstrap script is absent from captured inputs")
    return {
        "policy": POLICY,
        "selection": SELECTION,
        "selection_sha256": digest_json(SELECTION),
        "confcutdir": CONFCUTDIR,
        "script_sha256": script_sha,
    }


def expected_mutant_inputs(
    inputs: dict[str, Any], mutant: dict[str, Any], source: bytes
) -> tuple[dict[str, Any], str]:
    if hashlib.sha256(source).hexdigest() != mutant.get("source_sha256"):
        raise ValueError("stale clean source")
    lines = source.decode().splitlines(keepends=True)
    start, end = mutant["stmt_line"] - 1, mutant["stmt_end_line"]
    if "".join(lines[start:end]) != mutant.get("orig_span"):
        raise ValueError("stale original mutation span")
    changed = (
        "".join(lines[:start])
        + textwrap.indent(str(mutant["mut_stmt"]), " " * mutant["stmt_col"])
        + "\n"
        + "".join(lines[end:])
    ).encode()
    compile(changed, mutant["file"], "exec")
    if changed == source:
        raise ValueError("unchanged mutation")
    edited_sha = hashlib.sha256(changed).hexdigest()
    expected = copy.deepcopy(inputs)
    owners = [
        key
        for key, entries in expected.items()
        if isinstance(entries, dict) and mutant["file"] in entries
    ]
    if len(owners) != 1 or expected[owners[0]][mutant["file"]] != mutant["source_sha256"]:
        raise ValueError("mutation source is not uniquely bound to captured inputs")
    expected[owners[0]][mutant["file"]] = edited_sha
    return expected, edited_sha


def verify_phase(
    value: Any,
    selection: list[str],
    confcutdir: str | None,
    expected: dict[str, Any],
    clean: bool,
    baseline_nodes: set[str] | None = None,
) -> tuple[set[str], str]:
    from mutate_run import classify_pytest
    from verify_domain_receipts import EvidenceError, ancestors, inventory

    def need(ok: bool, reason: str) -> None:
        if not ok:
            raise EvidenceError(reason)

    if not isinstance(value, dict):
        raise EvidenceError("missing phase")
    need(
        value.get("selection") == selection
        and value.get("selection_sha256") == digest_json(selection),
        "wrong phase selection",
    )
    need(value.get("confcutdir") == confcutdir, "wrong phase collection boundary")
    need(
        value.get("inputs_before") == expected == value.get("inputs_after"),
        "stale or changed phase inputs",
    )
    need(value.get("cleanup_error") is None, "phase cleanup failed")
    receipt = value.get("pytest_receipt")
    if not isinstance(receipt, dict):
        raise EvidenceError("missing structured pytest receipt")
    nodes, _ = inventory(receipt, clean=clean)
    need(value.get("exit_code") == receipt["exit_code"], "phase exit mismatch")
    status = classify_pytest(receipt["exit_code"], receipt)
    need(value.get("status") == status, "phase status contradicts receipt")
    selectors = set(selection)
    hits: set[str] = set()
    for node in nodes:
        matching = selectors & ancestors(node)
        need(bool(matching), "phase node outside selection")
        hits.update(matching)
    if clean:
        need(status == "pass" and hits == selectors, "clean control incomplete")
    if baseline_nodes is not None:
        need(
            receipt["collected"] == len(baseline_nodes) and nodes <= baseline_nodes,
            "mutant phase differs from clean inventory",
        )
        if status == "pass":
            need(nodes == baseline_nodes, "passing mutant omitted clean nodes")
    return nodes, status


def verify(record: dict[str, Any], context: Any) -> dict[str, Any]:
    # Imported lazily: the native consumer itself imports the producer.
    from verify_domain_receipts import EvidenceError

    def need(ok: bool, reason: str) -> None:
        if not ok:
            raise EvidenceError(reason)

    need(
        record.get("status") in {"KILLED", "SURVIVED"},
        "not a definitive bootstrap attempt",
    )
    provenance = record.get("provenance")
    if not isinstance(provenance, dict):
        raise EvidenceError("missing bootstrap provenance")
    need(
        record.get("campaign_id") == context.campaign == digest_json(provenance),
        "stale campaign",
    )
    need(all(provenance.get(k) == v for k, v in context.inputs.items()), "stale inputs")
    need(
        all(provenance.get(k) == v for k, v in context.artifacts.items()),
        "stale artifacts",
    )
    cfg = provenance.get("config", {})
    need(
        provenance.get("schema") == 2 and cfg.get("full") is True,
        "bootstrap requires complete controls",
    )
    need(cfg.get("test_database_admin_sha256") == context.database_hash, "stale endpoint")
    need(
        cfg.get("domain_selection") == "all-coverers-or-declared-domain-suite-v1",
        "wrong native policy",
    )
    try:
        boot_config = configuration(context.inputs)
    except ValueError as exc:
        raise EvidenceError(str(exc)) from exc
    need(
        record.get("status_policy") == POLICY and cfg.get("bootstrap") == boot_config,
        "wrong bootstrap policy",
    )
    mutant = context.manifest.get(record.get("id"))
    need(
        isinstance(mutant, dict) and record.get("mutant_digest") == digest_json(mutant),
        "stale mutant",
    )
    need(
        all(record.get(k) == mutant.get(k) for k in ("file", "line", "kind", "detail", "tier")),
        "wrong mutant metadata",
    )
    need(callable(context.source_bytes), "missing independent clean source loader")
    try:
        mutant_inputs, edited_sha = expected_mutant_inputs(
            context.inputs, mutant, context.source_bytes(mutant["file"])
        )
    except (ValueError, SyntaxError, UnicodeError, OSError) as exc:
        raise EvidenceError(str(exc)) from exc
    need(record.get("edited_source_sha256") == edited_sha, "wrong edited source")
    native_selection = context.expected_selection(mutant)
    need(
        record.get("selection") == native_selection and record.get("selection_mode") == "complete",
        "wrong native selection",
    )
    need(
        record.get("selection_sha256") == digest_json(native_selection),
        "wrong native selection hash",
    )
    need(
        type(record.get("n_tests")) is int and record["n_tests"] == len(native_selection),
        "wrong native selector count",
    )

    boot = record.get("bootstrap")
    if not isinstance(boot, dict):
        raise EvidenceError("missing bootstrap phases")
    need(boot.get("configuration") == boot_config, "wrong bootstrap configuration")
    clean_boot, _ = verify_phase(boot.get("clean"), SELECTION, CONFCUTDIR, context.inputs, True)
    need(clean_boot == set(SELECTION), "bootstrap control must contain exactly one call")
    clean_native, _ = verify_phase(
        record.get("baseline"), native_selection, None, context.inputs, True
    )
    touched_boot, boot_status = verify_phase(
        boot.get("mutant"), SELECTION, CONFCUTDIR, mutant_inputs, False, clean_boot
    )
    if boot_status == "kill":
        need(
            record["status"] == "KILLED" and record.get("decisive_phase") == "bootstrap",
            "wrong bootstrap verdict",
        )
        need(
            record.get("native_mutant_attempted") is False and record.get("pytest_receipt") is None,
            "invented native mutant collection",
        )
        need(
            record.get("native_mutant")
            == {"status": "not-attempted", "reason": "bootstrap-assertion-kill"},
            "wrong native short-circuit marker",
        )
        return {
            "decisive_phase": "bootstrap",
            "concrete_native_clean_nodes": len(clean_native),
            "concrete_native_mutant_nodes": 0,
            "concrete_bootstrap_clean_nodes": len(clean_boot),
            "concrete_bootstrap_mutant_nodes": len(touched_boot),
            "selector_count": len(native_selection),
        }
    need(boot_status == "pass", "non-assertion bootstrap failure is not definitive")
    need(
        record.get("native_mutant_attempted") is True and record.get("decisive_phase") == "native",
        "native phase missing",
    )
    touched_native, native_status = verify_phase(
        record.get("native_mutant"),
        native_selection,
        None,
        mutant_inputs,
        False,
        clean_native,
    )
    need(
        record.get("pytest_receipt") == record["native_mutant"]["pytest_receipt"],
        "native top receipt mismatch",
    )
    need(
        native_status == {"KILLED": "kill", "SURVIVED": "pass"}[record["status"]],
        "wrong native verdict",
    )
    return {
        "decisive_phase": "native",
        "concrete_native_clean_nodes": len(clean_native),
        "concrete_native_mutant_nodes": len(touched_native),
        "concrete_bootstrap_clean_nodes": len(clean_boot),
        "concrete_bootstrap_mutant_nodes": len(touched_boot),
        "selector_count": len(native_selection),
    }
