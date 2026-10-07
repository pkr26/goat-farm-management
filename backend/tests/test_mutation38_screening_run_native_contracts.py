"""Native run construction conserves persisted confidence and safe diagnostics."""

from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import DataError

from app.db import get_sessionmaker
from app.models import ScreeningRun
from app.services.screening import pipeline

from .conftest import owner_with_farm
from .test_mutation38_screening_queue_native_contracts import _image


async def test_native_run_rounds_confidence_once_before_numeric_storage(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    async with get_sessionmaker()() as db:
        image = await _image(db, int(owner["X-Farm-Id"]), "native-confidence")
        run = pipeline._record_run(
            image,
            stage="GATE",
            provider="camera",
            model="goat-model",
            prompt_version="native",
            verdict="healthy",
            confidence=0.12349,
            latency_ms=1,
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)
        # Four-place rounding followed by PostgreSQL NUMERIC(4,3) rounding
        # turns this real confidence into .124; declared three-place .123
        # preserves the original nearest representable stored confidence.
        assert run.confidence == Decimal("0.123")
        saved = await db.scalar(select(ScreeningRun.confidence).where(ScreeningRun.id == run.id))
        assert saved == Decimal("0.123")


async def test_native_run_bounds_a_real_optional_error_diagnostic_to_two_thousand_characters(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    diagnostic = "Safe retained camera diagnostic: " + "x" * 2001
    async with get_sessionmaker()() as db:
        image = await _image(db, int(owner["X-Farm-Id"]), "native-diagnostic")
        run = pipeline._record_run(
            image,
            stage="GATE",
            provider="camera",
            model="goat-model",
            prompt_version="native",
            run_status="ERROR",
            error=diagnostic,
        )
        db.add(run)
        try:
            await db.commit()
        except DataError as exc:
            await db.rollback()
            pytest.fail(
                f"A bounded legitimate native run diagnostic must fit its real storage: {exc}"
            )
        await db.refresh(run)
        assert run.error == diagnostic[:2000] and len(run.error) == 2000
        assert (
            await db.scalar(select(ScreeningRun.error).where(ScreeningRun.id == run.id))
            == diagnostic[:2000]
        )
