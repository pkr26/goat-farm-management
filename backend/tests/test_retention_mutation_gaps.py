"""Mutation-campaign gap tests for the retention sweep boundaries (2026-09-30).

The surviving mutants were boundary and scoping drift in the screening-chain
selection: `created_at < cutoff` (strictly-older semantics) and the crop
selector's farm predicate. Both pinned exactly with a frozen clock.
"""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

import app.services.retention as retention_module
from app.db import get_sessionmaker
from app.models import ScreeningRetentionDeletion
from app.services.retention import run_retention_sweep
from app.services.screening.s3 import ScreeningStorage
from app.utils import utcnow

from .conftest import owner_with_farm
from .test_retention import _seed_chain, _settings, _table_counts


@pytest.fixture(autouse=True)
def _stub_permanent_object_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    """Boundary/scoping tests stay deterministic and never contact S3."""
    monkeypatch.setattr(
        ScreeningStorage,
        "delete_permanently",
        lambda _storage, _keys: None,
    )


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
    deleted_keys: list[str] = []
    monkeypatch.setattr(
        ScreeningStorage,
        "delete_permanently",
        lambda _storage, keys: deleted_keys.extend(keys),
    )

    async with get_sessionmaker()() as db:
        await _seed_chain(
            db, farm_id=farm_id, image_created_at=exactly_at_cutoff, key_suffix="edge"
        )
        await _seed_chain(db, farm_id=farm_id, image_created_at=just_past, key_suffix="past")
        await db.commit()

    async with get_sessionmaker()() as db:
        summary = await run_retention_sweep(db, settings)

    # Only the strictly-older chain drains; the exactly-at-cutoff one stays.
    assert summary.failed_farms == 0
    assert summary.screening_deletion_intents_staged == 1
    assert summary.screening_object_deletions_verified == 1
    assert summary.screening_images == 1
    assert summary.screening_crops == 1
    counts = await _table_counts(farm_id)
    assert counts["images"] == 1
    assert counts["crops"] == 1
    assert deleted_keys == [f"raw/{farm_id}/2026-01-01/BREEDING/past.jpg"]
    async with get_sessionmaker()() as db:
        assert list((await db.execute(select(ScreeningRetentionDeletion))).scalars()) == []


async def test_crop_deletion_is_counted_and_farm_scoped(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saga finalization explicitly deletes and counts only THIS farm's crop."""
    owner = await owner_with_farm(client, email="retention-crop-a@farm.in", farm_name="Farm A")
    other = await owner_with_farm(client, email="retention-crop-b@farm.in", farm_name="Farm B")
    farm_id = int(owner["X-Farm-Id"])
    other_farm_id = int(other["X-Farm-Id"])
    old = utcnow() - timedelta(days=200)
    young = utcnow() - timedelta(days=10)
    deleted_keys: list[str] = []
    monkeypatch.setattr(
        ScreeningStorage,
        "delete_permanently",
        lambda _storage, keys: deleted_keys.extend(keys),
    )

    async with get_sessionmaker()() as db:
        await _seed_chain(db, farm_id=farm_id, image_created_at=old, key_suffix="crop-a")
        await _seed_chain(db, farm_id=other_farm_id, image_created_at=young, key_suffix="crop-b")
        await db.commit()

    async with get_sessionmaker()() as db:
        summary = await run_retention_sweep(db, _settings())

    assert summary.failed_farms == 0
    assert summary.screening_deletion_intents_staged == 1
    assert summary.screening_object_deletions_verified == 1
    assert summary.screening_images == 1
    assert summary.screening_crops == 1
    assert (await _table_counts(farm_id))["crops"] == 0
    assert (await _table_counts(other_farm_id))["crops"] == 1
    assert deleted_keys == [f"raw/{farm_id}/2026-01-01/BREEDING/crop-a.jpg"]
    async with get_sessionmaker()() as db:
        assert list((await db.execute(select(ScreeningRetentionDeletion))).scalars()) == []
