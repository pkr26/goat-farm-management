#!/usr/bin/env python3
"""Fail CI when local SARIF contains an error or high-severity finding.

Private repositories without GitHub Code Security cannot upload CodeQL SARIF
to the Code Scanning API.  The analysis still produces a standard SARIF file,
so this small dependency-free gate makes the same findings enforceable in CI
instead of merely preserving them as an artifact.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, cast

HIGH_SECURITY_SEVERITY = 7.0
CODEQL_TOOL_NAMES = frozenset({"CodeQL", "CodeQL command-line toolchain"})
LEVELS = frozenset({"none", "note", "warning", "error"})


def _sarif_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix == ".sarif" else []
    if path.is_dir():
        return sorted(candidate for candidate in path.rglob("*.sarif") if candidate.is_file())
    return []


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _rule_for_result(run: Mapping[str, Any], result: Mapping[str, Any]) -> Mapping[str, Any]:
    driver = _mapping(_mapping(run.get("tool")).get("driver"))
    rules = driver.get("rules")
    rule_id = result.get("ruleId")
    if "ruleId" in result and (not isinstance(rule_id, str) or not rule_id):
        raise ValueError("SARIF result has an invalid ruleId")
    if "ruleIndex" in result:
        index = result["ruleIndex"]
        if type(index) is not int or not isinstance(rules, list) or not 0 <= index < len(rules):
            raise ValueError("SARIF result has an invalid ruleIndex")
        rule = _mapping(rules[index])
        if not isinstance(rule.get("id"), str) or not rule["id"]:
            raise ValueError("SARIF indexed rule has no valid id")
        if rule_id is not None and rule["id"] != rule_id:
            raise ValueError("SARIF ruleId and ruleIndex identify different rules")
        return rule
    if isinstance(rules, list) and isinstance(rule_id, str):
        for candidate in rules:
            rule = _mapping(candidate)
            if rule.get("id") == rule_id:
                return rule
    return {}


def _combined_properties(
    result: Mapping[str, Any], rule: Mapping[str, Any]
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Result properties override the rule defaults when both are present."""
    return _mapping(result.get("properties")), _mapping(rule.get("properties"))


def _property(
    result_properties: Mapping[str, Any], rule_properties: Mapping[str, Any], name: str
) -> object | None:
    return cast(object | None, result_properties.get(name, rule_properties.get(name)))


def _security_severity(value: object) -> float | None:
    try:
        severity = float(str(value))
    except (TypeError, ValueError):
        return None
    return severity if math.isfinite(severity) else None


def _result_reason(run: Mapping[str, Any], result: Mapping[str, Any]) -> str | None:
    rule = _rule_for_result(run, result)
    result_properties, rule_properties = _combined_properties(result, rule)
    default_configuration = _mapping(rule.get("defaultConfiguration"))
    level = result.get("level", default_configuration.get("level"))
    problem_severity = _property(result_properties, rule_properties, "problem.severity")
    if level == "error" or problem_severity == "error":
        return "error"
    security_severity = _security_severity(
        _property(result_properties, rule_properties, "security-severity")
    )
    if security_severity is not None and security_severity >= HIGH_SECURITY_SEVERITY:
        return f"security-severity={security_severity:g}"
    return None


def _location(result: Mapping[str, Any]) -> tuple[str, int | None]:
    locations = result.get("locations")
    if not isinstance(locations, list) or not locations:
        return "<no-location>", None
    physical = _mapping(_mapping(locations[0]).get("physicalLocation"))
    artifact = _mapping(physical.get("artifactLocation"))
    region = _mapping(physical.get("region"))
    uri = artifact.get("uri")
    line = region.get("startLine")
    return (
        uri if isinstance(uri, str) else "<no-location>",
        line if isinstance(line, int) else None,
    )


def _validate_run(run: object, context: str) -> Mapping[str, Any]:
    if not isinstance(run, dict):
        raise ValueError(f"{context}: run must be an object")
    driver = _mapping(_mapping(run.get("tool")).get("driver"))
    name = driver.get("name")
    if not isinstance(name, str) or name not in CODEQL_TOOL_NAMES:
        raise ValueError(f"{context}: expected a CodeQL tool run")
    # SARIF permits omitting invocation metadata. If supplied, it must prove
    # successful execution rather than silently override a failed invocation
    # with an empty results list (SARIF 2.1.0 section 3.20.14).
    if "invocations" in run:
        invocations = run["invocations"]
        if not isinstance(invocations, list) or not invocations:
            raise ValueError(f"{context}: invalid or empty invocations")
        for invocation in invocations:
            if (
                not isinstance(invocation, dict)
                or invocation.get("executionSuccessful") is not True
            ):
                raise ValueError(f"{context}: analysis invocation did not succeed")
            if invocation.get("processStartFailureMessage"):
                raise ValueError(f"{context}: analysis process failed to start")
            for key in ("toolExecutionNotifications", "toolConfigurationNotifications"):
                notifications = invocation.get(key, [])
                if not isinstance(notifications, list):
                    raise ValueError(f"{context}: malformed {key}")
                for notification in notifications:
                    if not isinstance(notification, dict):
                        raise ValueError(f"{context}: malformed {key} notification")
                    level = notification.get("level", "warning")
                    if not isinstance(level, str) or level not in LEVELS or level == "error":
                        raise ValueError(f"{context}: analysis reported an error in {key}")
    return run


def _find_blocking_results(files: Iterable[Path]) -> list[str]:
    findings: list[str] = []
    for file in files:
        try:
            document = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot parse SARIF file {file}: {exc}") from exc
        if _mapping(document).get("version") != "2.1.0":
            raise ValueError(f"SARIF file {file} must declare version 2.1.0")
        runs = _mapping(document).get("runs")
        if not isinstance(runs, list) or not runs:
            raise ValueError(f"SARIF file {file} has no nonempty runs array")
        for index, run in enumerate(runs):
            context = f"SARIF file {file}, run {index}"
            run_mapping = _validate_run(run, context)
            results = run_mapping.get("results")
            if not isinstance(results, list):
                raise ValueError(f"{context}: missing or invalid results array")
            for result in results:
                if not isinstance(result, dict):
                    raise ValueError(f"{context}: result must be an object")
                result_mapping = _mapping(result)
                rule = _rule_for_result(run_mapping, result_mapping)
                rule_id = result_mapping.get("ruleId", rule.get("id"))
                if not isinstance(rule_id, str) or not rule_id:
                    raise ValueError(f"{context}: result has no identifiable rule")
                for configuration in (
                    result_mapping,
                    _mapping(rule.get("defaultConfiguration")),
                ):
                    if "level" in configuration and (
                        not isinstance(configuration["level"], str)
                        or configuration["level"] not in LEVELS
                    ):
                        raise ValueError(f"{context}: invalid result level for {rule_id}")
                properties = _combined_properties(result_mapping, rule)
                severity = _property(*properties, "security-severity")
                if severity is not None and (
                    (parsed_severity := _security_severity(severity)) is None
                    or not 0 <= parsed_severity <= 10
                ):
                    raise ValueError(f"{context}: invalid security-severity for {rule_id}")
                reason = _result_reason(run_mapping, result_mapping)
                if reason is None:
                    continue
                uri, line = _location(result_mapping)
                location = f"{uri}:{line}" if line is not None else uri
                findings.append(
                    f"{file.name}: {rule_id or '<unknown-rule>'} at {location} ({reason})"
                )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sarif_path", type=Path, help="SARIF file or directory emitted by CodeQL")
    args = parser.parse_args(argv)
    files = _sarif_files(args.sarif_path)
    if not files:
        print(f"No SARIF files found at {args.sarif_path}", file=sys.stderr)
        return 2
    try:
        findings = _find_blocking_results(files)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not findings:
        print(f"SARIF gate passed: no error/high findings in {len(files)} file(s).")
        return 0
    print("SARIF gate rejected the following error/high findings:", file=sys.stderr)
    print("\n".join(findings), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
