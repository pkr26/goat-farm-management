"""Mutation-campaign gap tests for the retention sweep boundaries (2026-09-30).

The surviving mutants were boundary and scoping drift in the screening-chain
selection: `created_at < cutoff` (strictly-older semantics) and the crop
selector's farm predicate. Both pinned exactly with a frozen clock.
"""

from datetime import timedelta

import httpx
import pytest

import app.services.retention as retention_module
from app.db import get_sessionmaker
from app.services.retention import run_retention_sweep
from app.utils import utcnow

from .conftest import owner_with_farm
from .test_retention import _seed_chain, _settings, _table_counts


async def test_screening_chain_exactly_at_cutoff_is_kept_one_more_day(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retention deletes chains STRICTLY older than the cutoff: a chain aged
    exactly `retention_screening_days` survives (an off-by-one here would
    either leak retained PII or delete a day early)."""
    owner = await owner_with_farm(client, email="retention-edge@farm.in")
    farm_id = int(owner["X-Farm-Id"])

    frozen = utcnow()
    monkeypatch.setattr(retention_module, "utcnow", lambda: frozen)
    settings = _settings(retention_screening_days=180)
    exactly_at_cutoff = frozen - timedelta(days=180)
    just_past = exactly_at_cutoff - timedelta(microseconds=1)

    async with get_sessionmaker()() as db:
        await _seed_chain(
            db, farm_id=farm_id, image_created_at=exactly_at_cutoff, key_suffix="edge"
        )
        await _seed_chain(db, farm_id=farm_id, image_created_at=just_past, key_suffix="past")
        await db.commit()

    async with get_sessionmaker()() as db:
        summary = await run_retention_sweep(db, settings)

    # Only the strictly-older chain drains; the exactly-at-cutoff one stays.
    assert summary.screening_images == 1
    assert summary.screening_crops == 1
    counts = await _table_counts(farm_id)
    assert counts["images"] == 1
    assert counts["crops"] == 1


async def test_crop_deletion_is_counted_and_farm_scoped(client: httpx.AsyncClient) -> None:
    """The crop selector deletes THIS farm's crops directly (the summary
    counts them) — never an empty cross-farm predicate that silently relies
    on cascades."""
    owner = await owner_with_farm(client, email="retention-crop-a@farm.in", farm_name="Farm A")
    other = await owner_with_farm(client, email="retention-crop-b@farm.in", farm_name="Farm B")
    farm_id = int(owner["X-Farm-Id"])
    other_farm_id = int(other["X-Farm-Id"])
    old = utcnow() - timedelta(days=200)
    young = utcnow() - timedelta(days=10)

    async with get_sessionmaker()() as db:
        await _seed_chain(db, farm_id=farm_id, image_created_at=old, key_suffix="crop-a")
        await _seed_chain(db, farm_id=other_farm_id, image_created_at=young, key_suffix="crop-b")
        await db.commit()

    async with get_sessionmaker()() as db:
        summary = await run_retention_sweep(db, _settings())

    assert summary.screening_crops == 1  # farm A's crop, counted in the batch
    assert (await _table_counts(farm_id))["crops"] == 0
    assert (await _table_counts(other_farm_id))["crops"] == 1
