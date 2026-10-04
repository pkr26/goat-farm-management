#!/usr/bin/env python3
"""Compare a candidate OpenAPI document with an immutable earlier contract.

This intentionally checks compatibility, not snapshot freshness. CI obtains
the baseline from the pull request base commit (an immutable Git object), so
regenerating the candidate contract cannot also rewrite the comparison side.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal, cast

HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
Direction = Literal["input", "output", "neutral"]
JsonMap = dict[str, Any]
_MISSING = object()


def _mapping(value: object) -> JsonMap:
    return cast(JsonMap, value) if isinstance(value, dict) else {}


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _resolve(schema: JsonMap, document: JsonMap) -> JsonMap:
    reference = schema.get("$ref")
    if not isinstance(reference, str) or not reference.startswith("#/components/schemas/"):
        return schema
    name = reference.removeprefix("#/components/schemas/")
    return _mapping(_mapping(_mapping(document.get("components")).get("schemas")).get(name))


def _parameter_map(path_item: JsonMap, operation: JsonMap) -> dict[tuple[str, str], JsonMap]:
    parameters: dict[tuple[str, str], JsonMap] = {}
    for raw in [*_list(path_item.get("parameters")), *_list(operation.get("parameters"))]:
        parameter = _mapping(raw)
        name = parameter.get("name")
        location = parameter.get("in")
        if isinstance(name, str) and isinstance(location, str):
            parameters[(location, name)] = parameter
    return parameters


def _json_values(value: object) -> dict[str, Any]:
    """Return JSON values keyed by a stable representation, including objects."""
    return {json.dumps(item, sort_keys=True, separators=(",", ":")): item for item in _list(value)}


def _types(schema: JsonMap) -> set[str]:
    raw = schema.get("type")
    values = (
        {raw} if isinstance(raw, str) else {item for item in _list(raw) if isinstance(item, str)}
    )
    if schema.get("nullable") is True:
        values.add("null")
    return values


def _number(value: object) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _lower_bound(schema: JsonMap) -> tuple[int | float, bool] | None:
    exclusive = _number(schema.get("exclusiveMinimum"))
    inclusive = _number(schema.get("minimum"))
    bounds: list[tuple[int | float, bool]] = []
    if exclusive is not None:
        bounds.append((exclusive, True))
    if inclusive is not None:
        bounds.append((inclusive, False))
    return max(bounds, key=lambda bound: (bound[0], bound[1])) if bounds else None


def _upper_bound(schema: JsonMap) -> tuple[int | float, bool] | None:
    exclusive = _number(schema.get("exclusiveMaximum"))
    inclusive = _number(schema.get("maximum"))
    bounds: list[tuple[int | float, bool]] = []
    if exclusive is not None:
        bounds.append((exclusive, True))
    if inclusive is not None:
        bounds.append((inclusive, False))
    return min(bounds, key=lambda bound: (bound[0], not bound[1])) if bounds else None


def _lower_is_stricter(
    candidate: tuple[int | float, bool], baseline: tuple[int | float, bool]
) -> bool:
    return candidate[0] > baseline[0] or (
        candidate[0] == baseline[0] and candidate[1] and not baseline[1]
    )


def _upper_is_stricter(
    candidate: tuple[int | float, bool], baseline: tuple[int | float, bool]
) -> bool:
    return candidate[0] < baseline[0] or (
        candidate[0] == baseline[0] and candidate[1] and not baseline[1]
    )


def _constraint_changes(
    old: JsonMap,
    new: JsonMap,
    location: str,
    direction: Direction,
) -> list[str]:
    """Compare validation keywords using request/response set direction.

    Inputs must not accept fewer values than before. Outputs must not produce
    values outside the old documented set. Neutral comparisons are deliberately
    conservative, though operation traversal normally supplies a direction.
    """
    changes: list[str] = []
    old_types = _types(old)
    new_types = _types(new)
    if old_types or new_types:
        if direction == "input":
            incompatible_types = old_types - new_types if new_types else set()
        elif direction == "output":
            incompatible_types = new_types - old_types if old_types else new_types
        else:
            incompatible_types = old_types ^ new_types
        if incompatible_types:
            changes.append(
                f"{location}: accepted type set changed incompatibly "
                f"from {sorted(old_types)!r} to {sorted(new_types)!r}"
            )

    old_enum = _json_values(old.get("enum"))
    new_enum = _json_values(new.get("enum"))
    if old_enum or new_enum:
        if direction == "input":
            incompatible_enum = old_enum.keys() - new_enum.keys() if new_enum else old_enum.keys()
            label = "input enum values removed"
        elif direction == "output":
            incompatible_enum = new_enum.keys() - old_enum.keys() if old_enum else new_enum.keys()
            label = "new response enum values"
        else:
            incompatible_enum = old_enum.keys() ^ new_enum.keys()
            label = "enum values changed"
        if incompatible_enum:
            rendered = [(old_enum | new_enum)[key] for key in sorted(incompatible_enum)]
            changes.append(f"{location}: {label}: {rendered!r}")

    old_lower = _lower_bound(old)
    new_lower = _lower_bound(new)
    old_upper = _upper_bound(old)
    new_upper = _upper_bound(new)
    if (
        (
            direction == "input"
            and new_lower is not None
            and (old_lower is None or _lower_is_stricter(new_lower, old_lower))
        )
        or (
            direction == "output"
            and old_lower is not None
            and (new_lower is None or _lower_is_stricter(old_lower, new_lower))
        )
        or (direction == "neutral" and old_lower != new_lower)
    ):
        changes.append(f"{location}: lower bound changed incompatibly")
    if (
        (
            direction == "input"
            and new_upper is not None
            and (old_upper is None or _upper_is_stricter(new_upper, old_upper))
        )
        or (
            direction == "output"
            and old_upper is not None
            and (new_upper is None or _upper_is_stricter(old_upper, new_upper))
        )
        or (direction == "neutral" and old_upper != new_upper)
    ):
        changes.append(f"{location}: upper bound changed incompatibly")

    for minimum_key in ("minLength", "minItems", "minProperties", "minContains"):
        old_value = _number(old.get(minimum_key))
        new_value = _number(new.get(minimum_key))
        incompatible = (
            (
                direction == "input"
                and new_value is not None
                and (old_value is None or new_value > old_value)
            )
            or (
                direction == "output"
                and old_value is not None
                and (new_value is None or new_value < old_value)
            )
            or (direction == "neutral" and old_value != new_value)
        )
        if incompatible:
            changes.append(f"{location}: {minimum_key} changed incompatibly")
    for maximum_key in ("maxLength", "maxItems", "maxProperties", "maxContains"):
        old_value = _number(old.get(maximum_key))
        new_value = _number(new.get(maximum_key))
        incompatible = (
            (
                direction == "input"
                and new_value is not None
                and (old_value is None or new_value < old_value)
            )
            or (
                direction == "output"
                and old_value is not None
                and (new_value is None or new_value > old_value)
            )
            or (direction == "neutral" and old_value != new_value)
        )
        if incompatible:
            changes.append(f"{location}: {maximum_key} changed incompatibly")

    for keyword in (
        "const",
        "pattern",
        "format",
        "multipleOf",
        "contentEncoding",
        "contentMediaType",
        "not",
        "contains",
        "propertyNames",
        "dependentRequired",
        "dependentSchemas",
        "if",
        "then",
        "else",
    ):
        old_value = old.get(keyword, _MISSING)
        new_value = new.get(keyword, _MISSING)
        incompatible = (
            (direction == "input" and new_value is not _MISSING and new_value != old_value)
            or (direction == "output" and old_value is not _MISSING and new_value != old_value)
            or (direction == "neutral" and old_value != new_value)
        )
        if incompatible:
            changes.append(f"{location}: {keyword} changed incompatibly")

    old_unique = old.get("uniqueItems") is True
    new_unique = new.get("uniqueItems") is True
    if (
        (direction == "input" and new_unique and not old_unique)
        or (direction == "output" and old_unique and not new_unique)
        or (direction == "neutral" and old_unique != new_unique)
    ):
        changes.append(f"{location}: uniqueItems changed incompatibly")
    if old.get("discriminator", _MISSING) != new.get("discriminator", _MISSING):
        changes.append(f"{location}: discriminator changed")
    return changes


def _open_object_policy(value: object) -> bool:
    return value is _MISSING or value is True


def _object_policy_changes(
    old: JsonMap,
    new: JsonMap,
    baseline: JsonMap,
    candidate: JsonMap,
    location: str,
    direction: Direction,
    seen: set[tuple[str, str, Direction]],
) -> list[str]:
    changes: list[str] = []
    for keyword in ("additionalProperties", "unevaluatedProperties"):
        old_value = old.get(keyword, _MISSING)
        new_value = new.get(keyword, _MISSING)
        old_open = _open_object_policy(old_value)
        new_open = _open_object_policy(new_value)
        old_schema = _mapping(old_value)
        new_schema = _mapping(new_value)
        if direction == "input" and old_open and not new_open:
            changes.append(f"{location}: {keyword} now restricts accepted input properties")
        elif direction == "output" and not old_open and new_open:
            changes.append(f"{location}: {keyword} now permits undocumented response properties")
        elif direction == "neutral" and old_value != new_value:
            changes.append(f"{location}: {keyword} changed")
        if old_schema and new_schema:
            changes.extend(
                _schema_changes(
                    old_schema,
                    new_schema,
                    baseline,
                    candidate,
                    f"{location}.{keyword}",
                    direction,
                    seen,
                )
            )
    return changes


def _schema_changes(
    baseline_schema: JsonMap,
    candidate_schema: JsonMap,
    baseline: JsonMap,
    candidate: JsonMap,
    location: str,
    direction: Direction,
    seen: set[tuple[str, str, Direction]],
) -> list[str]:
    baseline_ref = baseline_schema.get("$ref")
    candidate_ref = candidate_schema.get("$ref")
    pair = (
        str(baseline_ref or id(baseline_schema)),
        str(candidate_ref or id(candidate_schema)),
        direction,
    )
    if pair in seen:
        return []
    seen.add(pair)
    old = _resolve(baseline_schema, baseline)
    new = _resolve(candidate_schema, candidate)
    if not old:
        return []
    if not new:
        return [f"{location}: schema was removed or its reference no longer resolves"]

    changes = _constraint_changes(old, new, location, direction)
    changes.extend(
        _object_policy_changes(
            old,
            new,
            baseline,
            candidate,
            location,
            direction,
            seen,
        )
    )

    old_properties = _mapping(old.get("properties"))
    new_properties = _mapping(new.get("properties"))
    for name in sorted(old_properties.keys() - new_properties.keys()):
        changes.append(f"{location}.properties.{name}: property removed")
    old_required = {item for item in _list(old.get("required")) if isinstance(item, str)}
    new_required = {item for item in _list(new.get("required")) if isinstance(item, str)}
    if direction == "input":
        for name in sorted(new_required - old_required):
            changes.append(f"{location}.required: new required input property {name!r}")
    elif direction == "output":
        for name in sorted(old_required - new_required):
            changes.append(
                f"{location}.required: response property {name!r} is no longer guaranteed"
            )
    for name in sorted(old_properties.keys() & new_properties.keys()):
        changes.extend(
            _schema_changes(
                _mapping(old_properties[name]),
                _mapping(new_properties[name]),
                baseline,
                candidate,
                f"{location}.properties.{name}",
                direction,
                seen,
            )
        )

    old_items = _mapping(old.get("items"))
    if old_items:
        changes.extend(
            _schema_changes(
                old_items,
                _mapping(new.get("items")),
                baseline,
                candidate,
                f"{location}.items",
                direction,
                seen,
            )
        )

    for keyword in ("allOf", "anyOf", "oneOf"):
        old_variants = [_mapping(value) for value in _list(old.get(keyword))]
        new_variants = [_mapping(value) for value in _list(new.get(keyword))]
        variants_broke = (
            keyword in {"anyOf", "oneOf"}
            and (
                (direction == "input" and len(new_variants) < len(old_variants))
                or (direction == "output" and len(new_variants) > len(old_variants))
            )
        ) or (
            keyword == "allOf"
            and (
                (direction == "input" and len(new_variants) > len(old_variants))
                or (direction == "output" and len(new_variants) < len(old_variants))
            )
        )
        if direction == "neutral" and len(new_variants) != len(old_variants):
            variants_broke = True
        if variants_broke:
            counts = f"{len(old_variants)} -> {len(new_variants)}"
            changes.append(f"{location}.{keyword}: variant set changed incompatibly ({counts})")
        for index, old_variant in enumerate(old_variants[: len(new_variants)]):
            changes.extend(
                _schema_changes(
                    old_variant,
                    new_variants[index],
                    baseline,
                    candidate,
                    f"{location}.{keyword}[{index}]",
                    direction,
                    seen,
                )
            )
    return changes


def _content_changes(
    old_content: object,
    new_content: object,
    baseline: JsonMap,
    candidate: JsonMap,
    location: str,
    direction: Direction,
) -> list[str]:
    old_media = _mapping(old_content)
    new_media = _mapping(new_content)
    changes: list[str] = []
    for media_type in sorted(old_media.keys() - new_media.keys()):
        changes.append(f"{location}: media type {media_type!r} removed")
    for media_type in sorted(old_media.keys() & new_media.keys()):
        changes.extend(
            _schema_changes(
                _mapping(_mapping(old_media[media_type]).get("schema")),
                _mapping(_mapping(new_media[media_type]).get("schema")),
                baseline,
                candidate,
                f"{location}.{media_type}",
                direction,
                set(),
            )
        )
    return changes


def _effective_security(document: JsonMap, operation: JsonMap) -> object:
    return operation["security"] if "security" in operation else document.get("security")


def find_breaking_changes(baseline: JsonMap, candidate: JsonMap) -> list[str]:
    """Return stable, human-readable compatibility violations."""
    changes: list[str] = []
    old_paths = _mapping(baseline.get("paths"))
    new_paths = _mapping(candidate.get("paths"))
    for path in sorted(old_paths):
        old_path_item = _mapping(old_paths[path])
        new_path_item = _mapping(new_paths.get(path))
        if not new_path_item:
            changes.append(f"paths.{path}: path removed")
            continue
        for method in sorted(set(old_path_item) & HTTP_METHODS):
            old_operation = _mapping(old_path_item[method])
            new_operation = _mapping(new_path_item.get(method))
            location = f"paths.{path}.{method}"
            if not new_operation:
                changes.append(f"{location}: operation removed")
                continue
            if old_operation.get("operationId") != new_operation.get("operationId"):
                changes.append(f"{location}: operationId changed")
            if _effective_security(baseline, old_operation) != _effective_security(
                candidate, new_operation
            ):
                changes.append(f"{location}: security requirements changed")

            old_parameters = _parameter_map(old_path_item, old_operation)
            new_parameters = _parameter_map(new_path_item, new_operation)
            for key in sorted(old_parameters.keys() - new_parameters.keys()):
                changes.append(f"{location}.parameters.{key[0]}.{key[1]}: parameter removed")
            for key in sorted(new_parameters.keys() - old_parameters.keys()):
                if new_parameters[key].get("required") is True:
                    changes.append(
                        f"{location}.parameters.{key[0]}.{key[1]}: new required parameter"
                    )
            for key in sorted(old_parameters.keys() & new_parameters.keys()):
                old_parameter = old_parameters[key]
                new_parameter = new_parameters[key]
                parameter_location = f"{location}.parameters.{key[0]}.{key[1]}"
                if (
                    old_parameter.get("required") is not True
                    and new_parameter.get("required") is True
                ):
                    changes.append(f"{parameter_location}: optional parameter became required")
                changes.extend(
                    _schema_changes(
                        _mapping(old_parameter.get("schema")),
                        _mapping(new_parameter.get("schema")),
                        baseline,
                        candidate,
                        parameter_location,
                        "input",
                        set(),
                    )
                )

            old_body = _mapping(old_operation.get("requestBody"))
            new_body = _mapping(new_operation.get("requestBody"))
            if old_body and not new_body:
                changes.append(f"{location}.requestBody: request body removed")
            elif old_body:
                if old_body.get("required") is not True and new_body.get("required") is True:
                    changes.append(f"{location}.requestBody: optional request body became required")
                changes.extend(
                    _content_changes(
                        old_body.get("content"),
                        new_body.get("content"),
                        baseline,
                        candidate,
                        f"{location}.requestBody.content",
                        "input",
                    )
                )
            elif new_body.get("required") is True:
                changes.append(f"{location}.requestBody: new required request body")

            old_responses = _mapping(old_operation.get("responses"))
            new_responses = _mapping(new_operation.get("responses"))
            for status in sorted(old_responses.keys() - new_responses.keys()):
                changes.append(f"{location}.responses.{status}: response removed")
            for status in sorted(old_responses.keys() & new_responses.keys()):
                old_response = _mapping(old_responses[status])
                new_response = _mapping(new_responses[status])
                response_location = f"{location}.responses.{status}"
                old_headers = _mapping(old_response.get("headers"))
                new_headers = _mapping(new_response.get("headers"))
                for header in sorted(old_headers.keys() - new_headers.keys()):
                    changes.append(
                        f"{response_location}.headers.{header}: documented header removed"
                    )
                for header in sorted(old_headers.keys() & new_headers.keys()):
                    changes.extend(
                        _schema_changes(
                            _mapping(_mapping(old_headers[header]).get("schema")),
                            _mapping(_mapping(new_headers[header]).get("schema")),
                            baseline,
                            candidate,
                            f"{response_location}.headers.{header}",
                            "output",
                            set(),
                        )
                    )
                changes.extend(
                    _content_changes(
                        old_response.get("content"),
                        new_response.get("content"),
                        baseline,
                        candidate,
                        f"{response_location}.content",
                        "output",
                    )
                )

    old_components = _mapping(_mapping(baseline.get("components")).get("schemas"))
    new_components = _mapping(_mapping(candidate.get("components")).get("schemas"))
    for name in sorted(old_components.keys() - new_components.keys()):
        changes.append(f"components.schemas.{name}: schema removed")
    # Used component schemas are compared above through each operation with
    # their actual input/output direction. Deep-comparing them again as
    # directionless definitions would reject safe request expansions and safe
    # response narrowing. Removed definitions remain breaking even if a
    # consumer references one only out-of-band.
    old_schemes = _mapping(_mapping(baseline.get("components")).get("securitySchemes"))
    new_schemes = _mapping(_mapping(candidate.get("components")).get("securitySchemes"))
    for name in sorted(old_schemes.keys() - new_schemes.keys()):
        changes.append(f"components.securitySchemes.{name}: security scheme removed")
    for name in sorted(old_schemes.keys() & new_schemes.keys()):
        if old_schemes[name] != new_schemes[name]:
            changes.append(f"components.securitySchemes.{name}: security scheme changed")
    return sorted(set(changes))


def _load(path: Path) -> JsonMap:
    value: object = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return cast(JsonMap, value)


def apply_exact_waivers(
    changes: list[str], baseline_bytes: bytes, candidate_bytes: bytes, document: JsonMap
) -> tuple[list[str], int]:
    """Apply only waivers bound to the exact before/after document hashes."""
    if document.get("schema") != 1:
        raise ValueError("OpenAPI compatibility waiver file has an unsupported schema")
    baseline_hash = hashlib.sha256(baseline_bytes).hexdigest()
    candidate_hash = hashlib.sha256(candidate_bytes).hexdigest()
    remaining = list(changes)
    waived = 0
    for raw in _list(document.get("waivers")):
        waiver = _mapping(raw)
        if (
            waiver.get("baseline_sha256") != baseline_hash
            or waiver.get("candidate_sha256") != candidate_hash
        ):
            continue
        suffix = waiver.get("change_suffix")
        expected = waiver.get("expected_matches")
        reason = waiver.get("reason")
        if (
            not isinstance(suffix, str)
            or not suffix
            or not isinstance(expected, int)
            or expected < 1
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            raise ValueError("OpenAPI compatibility waiver is incomplete")
        matches = [change for change in remaining if change.endswith(suffix)]
        if len(matches) != expected:
            raise ValueError(
                f"OpenAPI compatibility waiver expected {expected} exact matches, "
                f"found {len(matches)}"
            )
        matched = set(matches)
        remaining = [change for change in remaining if change not in matched]
        waived += len(matches)
    return remaining, waived


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--waivers", type=Path)
    args = parser.parse_args(argv)
    baseline_bytes = args.baseline.read_bytes()
    candidate_bytes = args.candidate.read_bytes()
    baseline: object = json.loads(baseline_bytes)
    candidate: object = json.loads(candidate_bytes)
    if not isinstance(baseline, dict) or not isinstance(candidate, dict):
        raise TypeError("OpenAPI documents must contain JSON objects")
    changes = find_breaking_changes(baseline, candidate)
    waived = 0
    if args.waivers is not None:
        changes, waived = apply_exact_waivers(
            changes,
            baseline_bytes,
            candidate_bytes,
            _load(args.waivers),
        )
    if not changes:
        suffix = f" ({waived} exact-hash-bound changes explicitly waived)" if waived else ""
        print(f"OpenAPI compatibility holds against the immutable baseline{suffix}")
        return 0
    print("Breaking OpenAPI changes detected:")
    for change in changes:
        print(f"- {change}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
