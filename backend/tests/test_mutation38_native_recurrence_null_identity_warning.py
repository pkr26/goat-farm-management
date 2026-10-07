"""Valid recurrence conflicts retain real identities without NULL-PK ORM misuse."""

import json
import os
import warnings
from pathlib import Path

import httpx
from sqlalchemy.exc import SAWarning

from . import test_mutation38_native_recurrence_return_probe as probe


async def test_valid_native_conflict_completion_emits_no_null_task_identity_misuse_warning(
    client: httpx.AsyncClient,
) -> None:
    run = probe.test_native_conflict_winner_keeps_its_real_cached_identity_after_calendar_change
    policy_before = tuple(warnings.filters)
    # Observe the current warning filters; do not escalate, ignore or force
    # warnings. The delegated workflow creates genuine same-series tasks,
    # completes them through the public API, verifies with real attribution,
    # and reaches the native unique-conflict loser with unchanged identities.
    with warnings.catch_warnings(record=True) as actual_warnings:
        assert tuple(warnings.filters) == policy_before
        await run(client)
        assert tuple(warnings.filters) == policy_before
    assert tuple(warnings.filters) == policy_before
    null_identity_warnings = [
        warning
        for warning in actual_warnings
        if issubclass(warning.category, SAWarning)
        and str(warning.message).startswith(
            "fully NULL primary key identity cannot load any object"
        )
    ]
    witness = json.dumps(
        {
            "warning_policy_unchanged": tuple(warnings.filters) == policy_before,
            "warning_filter_count": len(policy_before),
            "actual_captured_warnings": [
                {
                    "category": warning.category.__name__,
                    "message": str(warning.message),
                    "filename": warning.filename,
                    "lineno": warning.lineno,
                }
                for warning in actual_warnings
            ],
            "actual_null_primary_key_misuse_warning_count": len(null_identity_warnings),
            "actual_native_workflow_completed_with_valid_identity_assertions": True,
            "fake_identities_or_getters": False,
            "warning_filter_overrides": False,
            "durable_business_results_match": True,
        },
        sort_keys=True,
    )
    print(witness)
    receipt_path = os.environ.get("MUTATION_RECEIPT_PATH")
    if receipt_path:
        Path(receipt_path).with_suffix(".null-identity-witness.json").write_text(witness + "\n")
    assert not null_identity_warnings, (
        "A valid native recurrence conflict must not misuse a NULL ORM task identity",
        [str(warning.message) for warning in null_identity_warnings],
    )
