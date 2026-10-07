"""Breeding reads and output validation preserve committed reproductive facts."""

import asyncio
from datetime import date

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import breeding as breeding_api
from app.db import get_sessionmaker
from app.models import BreedingRecord
from app.schemas.breeding import BreedingRecordOut
from app.services.breeding import mark_aborted
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import bred_doe, pregnant_doe


async def test_breeding_detail_reads_the_committed_pregnancy_while_a_real_abort_is_uncommitted(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client)
    _doe, _buck, pregnancy = await pregnant_doe(client, owner, "READ-LOSS-DOE")
    record_id = int(pregnancy["id"])
    prepared = asyncio.Event()
    allow_commit = asyncio.Event()
    original_abort = mark_aborted

    async def pause_prepared_abort(
        db: AsyncSession,
        record: BreedingRecord,
        *,
        loss_date: date,
        loss_cause: str,
        loss_notes: str | None,
        recorded_by_id: int,
        allow_late_administrative_close: bool = False,
    ) -> BreedingRecord:
        result = await original_abort(
            db,
            record,
            loss_date=loss_date,
            loss_cause=loss_cause,
            loss_notes=loss_notes,
            recorded_by_id=recorded_by_id,
            allow_late_administrative_close=allow_late_administrative_close,
        )
        if record.id == record_id:
            # The real endpoint has locked Animal then BreedingRecord and
            # performed the actual loss/task/bucket cascade. Hold its commit.
            await db.flush()
            assert result.outcome == "ABORTED"
            prepared.set()
            await allow_commit.wait()
        return result

    monkeypatch.setattr(breeding_api, "mark_aborted", pause_prepared_abort)
    writer = asyncio.create_task(
        client.post(
            f"/api/breeding/{record_id}/abort",
            headers=owner,
            json={"loss_date": today().isoformat(), "cause": "INJURY"},
        )
    )
    try:
        await asyncio.wait_for(prepared.wait(), timeout=15)
        try:
            response = await asyncio.wait_for(
                client.get(f"/api/breeding/{record_id}", headers=owner), timeout=5
            )
        except TimeoutError:
            pytest.fail("A pregnancy detail read waited for the unrelated writer's commit")
        assert response.status_code == 200, response.text
        assert response.json()["outcome"] == "CONFIRMED_PREGNANT"
        assert response.json()["loss_date"] is None
    finally:
        allow_commit.set()
        aborted = await asyncio.wait_for(writer, timeout=15)
    assert aborted.status_code == 200, aborted.text
    assert aborted.json()["outcome"] == "ABORTED"
    committed = await client.get(f"/api/breeding/{record_id}", headers=owner)
    assert committed.status_code == 200, committed.text
    assert committed.json()["outcome"] == "ABORTED"


async def test_breeding_output_validates_a_real_committed_record_and_its_wire_facts(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    _doe, _buck, wire = await bred_doe(client, owner, "ORM-SERVICE-DOE")
    record_id = int(wire["id"])
    assert wire["has_kidding"] is False and wire["outcome"] == "PENDING"
    async with get_sessionmaker()() as db:
        record = (
            await db.execute(select(BreedingRecord).where(BreedingRecord.id == record_id))
        ).scalar_one()
        try:
            output = BreedingRecordOut.model_validate(record)
        except ValidationError as exc:
            pytest.fail(f"BreedingRecordOut rejected the real committed record: {exc}")
        # Display tags are enriched separately by breeding_out; compare all
        # persisted/public reproductive facts and the no-kidding default.
        actual = output.model_dump(mode="json", exclude={"doe_tag", "buck_tag"})
        expected = {key: value for key, value in wire.items() if key not in {"doe_tag", "buck_tag"}}
        assert actual == expected


async def test_loss_notes_are_trimmed_before_the_4000_character_admission_limit(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    _doe, _buck, pregnancy = await pregnant_doe(client, owner, "LOSS-NOTES-DOE")
    record_id = int(pregnancy["id"])
    notes = "X" * 4000
    response = await client.post(
        f"/api/breeding/{record_id}/abort",
        headers=owner,
        json={"loss_date": today().isoformat(), "cause": "INJURY", "notes": "  " + notes + "\n"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["loss_notes"] == notes
    assert response.json()["outcome"] == "ABORTED"
    async with get_sessionmaker()() as db:
        record = await db.get(BreedingRecord, record_id)
        assert record is not None and record.loss_notes == notes
