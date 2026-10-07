"""The real breeding audit must describe an override only when one occurred."""

import logging
from datetime import timedelta

import httpx
import pytest

from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import (
    fail_cycle,
    get_animal,
    iso,
    make_breeding,
    make_buck,
    make_doe,
)


def override_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "app.services.breeding"
        and record.getMessage().startswith("Cull-rule override:")
    ]


async def test_owner_breeding_audit_claims_only_a_real_cull_candidate_override(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, "AUDIT-CULL-DOE")
    buck = await make_buck(client, owner, "AUDIT-CULL-BUCK")
    assert (await get_animal(client, owner, doe["id"]))["cull_candidate"] is False
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="app.services.breeding"):
        first = await make_breeding(
            client,
            owner,
            doe["id"],
            buck["id"],
            breeding_date=iso(today() - timedelta(days=120)),
        )
    assert override_messages(caplog) == []

    # Two actual negative scans create the cull flag through the audited
    # workflow. A genuine owner override is the positive capture control.
    await fail_cycle(client, owner, first["id"])
    second = await make_breeding(
        client,
        owner,
        doe["id"],
        buck["id"],
        breeding_date=iso(today() - timedelta(days=60)),
    )
    await fail_cycle(client, owner, second["id"])
    assert (await get_animal(client, owner, doe["id"]))["cull_candidate"] is True
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="app.services.breeding"):
        third = await make_breeding(client, owner, doe["id"], buck["id"])
    assert third["outcome"] == "PENDING"
    messages = override_messages(caplog)
    assert len(messages) == 1
    assert "cull-candidate doe AUDIT-CULL-DOE" in messages[0]
    assert f"(farm {owner['X-Farm-Id']})" in messages[0]
