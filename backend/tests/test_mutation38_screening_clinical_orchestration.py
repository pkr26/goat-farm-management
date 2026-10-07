"""Actual clinical cascades conserve each region, paid attempt and deferred refinement."""

import json
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import MultipleResultsFound

from app.db import get_sessionmaker
from app.models import Farm, ScreeningFinding, ScreeningImage, ScreeningRun
from app.services.screening.pipeline import (
    CycleSummary,
    _add_finding,
    _prior_coverage_safety_status,
    _run_cascade,
    run_screening_cycle,
)
from app.services.screening.providers import ProviderAnswer, ProviderError
from app.services.screening.rotation import ProviderRotation
from app.utils import today

from .conftest import owner_with_farm
from .test_screening import CountingProvider, FakeStorage, _cycle_settings, _jpeg_bytes
from .type_helpers import Headers


class _RegionalProvider(CountingProvider):
    def __init__(self, name: str, mode: str = "ordinary") -> None:
        super().__init__(name=name, model="model-" + name)
        self.mode = mode
        self.prompts: list[str] = []

    async def complete(self, image_jpeg: bytes, system_prompt: str) -> ProviderAnswer:
        self.calls += 1
        self.prompts.append(system_prompt)
        if self.mode == "down":
            raise ProviderError("Actual controlled provider outage")
        if "veterinary specialist" in system_prompt:
            skin = "skin, lips" in system_prompt
            if skin and self.mode == "skin-down":
                raise ProviderError("Actual controlled skin-specialist outage")
            text = json.dumps(
                {
                    "conditions": [
                        {
                            "disease": "ORF" if skin else "PINK_EYE",
                            "confidence": 0.91234,
                            "severity": "moderate",
                            "note": "Visible specialist assessment",
                        }
                    ]
                }
            )
        else:
            healthy = self.mode == "healthy"
            text = json.dumps(
                {
                    "flagged": not healthy,
                    "quality_problem": self.mode == "quality",
                    "confidence": 0.8,
                    "observations": []
                    if healthy
                    else [
                        {
                            "region": "mouth",
                            "label": "Visible lip crust",
                            "confidence": 0.66667,
                            "note": "Mouth observation",
                        },
                        {
                            "region": "eye",
                            "label": "Visible eye discharge",
                            "confidence": 0.77777,
                            "note": "Eye observation",
                        },
                    ],
                }
            )
        return ProviderAnswer(
            text=text,
            provider="served-router-alias" if self.mode == "alias" else self.name,
            model=self.model,
            latency_ms=1,
        )


async def _regional_cycle(
    client: httpx.AsyncClient,
    primary: _RegionalProvider,
    *,
    secondary: _RegionalProvider | None = None,
    cap: int = 0,
    providers: list[_RegionalProvider] | None = None,
) -> tuple[Headers, int, CycleSummary]:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    day = today()
    key = f"raw/{farm_id}/{day.isoformat()}/regional-cascade.jpg"
    storage = FakeStorage(objects={key: _jpeg_bytes(160, 80)})
    settings = _cycle_settings()
    settings.screening_daily_call_budget_per_farm = cap
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        local_day = today(farm.timezone)
        rotation_members = providers or [primary]
        if secondary is not None:
            rotation_members = (
                [primary, secondary] if local_day.toordinal() % 2 == 0 else [secondary, primary]
            )
        rotation = ProviderRotation(rotation_members)
        image = ScreeningImage(
            farm_id=farm_id,
            s3_bucket=storage.bucket,
            s3_key=key,
            captured_date=day,
            status="PENDING",
        )
        db.add(image)
        await db.commit()
        try:
            summary = await run_screening_cycle(db, settings, storage, rotation)
        except (AttributeError, TypeError) as exc:
            pytest.fail(f"A schema-valid actual native clinical cascade must complete: {exc}")
        assert summary.claimed == 1
        return owner, image.id, summary


@pytest.mark.parametrize("label_length", [80, 81], ids=["column-boundary", "bounded-native-label"])
@pytest.mark.parametrize("confidence", [None, 0.91234], ids=["absent", "precise"])
async def test_native_finding_builder_bounds_text_and_conserves_nullable_three_decimal_evidence(
    client: httpx.AsyncClient, label_length: int, confidence: float | None
) -> None:
    _, image_id, _ = await _regional_cycle(client, _RegionalProvider("clinical-source"))
    async with get_sessionmaker()() as db:
        image = await db.get(ScreeningImage, image_id)
        assert image is not None
        gate = (
            await db.execute(
                select(ScreeningRun).where(
                    ScreeningRun.image_id == image_id, ScreeningRun.stage == "GATE"
                )
            )
        ).scalar_one()
        try:
            finding = _add_finding(
                image,
                gate.id,
                region="mouth",
                label="L" * label_length,
                confidence=confidence,
                note="Actual native bounded assessment",
            )
        except TypeError as exc:
            pytest.fail(f"A native nullable finding confidence must remain representable: {exc}")
        assert finding.label == "L" * 80
        assert finding.confidence == (None if confidence is None else 0.912)
        db.add(finding)
        await db.commit()
        await db.refresh(finding)
        assert finding.label == "L" * 80 and finding.confidence == (
            None if confidence is None else Decimal("0.912")
        )


async def test_failed_rotation_journals_each_actual_provider_model_and_paid_attempt(
    client: httpx.AsyncClient,
) -> None:
    providers = [_RegionalProvider(name, "down") for name in ["failed-a", "failed-b", "failed-c"]]
    _, image_id, summary = await _regional_cycle(client, providers[0], providers=providers)
    assert summary.errors == 1 and sum(provider.calls for provider in providers) == 3
    async with get_sessionmaker()() as db:
        rows = list(
            (
                await db.execute(select(ScreeningRun).where(ScreeningRun.image_id == image_id))
            ).scalars()
        )
        assert len(rows) == 3
        assert {(row.provider, row.model, row.stage, row.run_status) for row in rows} == {
            (provider.name, provider.model, "GATE", "ERROR") for provider in providers
        }


async def test_one_specialist_outage_preserves_its_region_and_continues_the_next_kind(
    client: httpx.AsyncClient,
) -> None:
    provider = _RegionalProvider("partial-specialist", "skin-down")
    _, image_id, summary = await _regional_cycle(client, provider)
    assert summary.flagged == 1 and provider.calls == 3
    async with get_sessionmaker()() as db:
        rows = list(
            (
                await db.execute(
                    select(ScreeningFinding)
                    .join(ScreeningRun, ScreeningRun.id == ScreeningFinding.run_id)
                    .where(ScreeningRun.image_id == image_id)
                    .order_by(ScreeningFinding.id)
                )
            ).scalars()
        )
        assert {(row.region, row.label) for row in rows} == {
            ("mouth", "Visible lip crust"),
            ("eye", "PINK_EYE"),
        }
        assert len(rows) == 2


@pytest.mark.parametrize("cap", [1, 3], ids=["refinement-denied", "cross-check-denied"])
async def test_real_paid_budget_defers_once_and_preserves_gate_provenance_and_regions(
    client: httpx.AsyncClient, cap: int
) -> None:
    provider = _RegionalProvider("budget-primary")
    checker = _RegionalProvider("budget-checker", "healthy")
    _, image_id, summary = await _regional_cycle(client, provider, secondary=checker, cap=cap)
    assert summary.flagged == 1 and summary.budget_deferred == 1
    assert provider.calls == cap and checker.calls == 0
    async with get_sessionmaker()() as db:
        runs = list(
            (
                await db.execute(select(ScreeningRun).where(ScreeningRun.image_id == image_id))
            ).scalars()
        )
        assert len(runs) == cap and all(run.run_status == "OK" for run in runs)
        gates = [run for run in runs if run.stage == "GATE"]
        assert len(gates) == 1
        detail = gates[0].detail
        assert detail is not None and detail.get("budget_limited") is True
        assert detail.get("served_by") == "budget-primary" and detail.get("observation_count") == 2
        assert detail.get("coverage_safety_pass") is False
        assert isinstance(detail.get("response_sha256"), str)
        findings = list(
            (
                await db.execute(
                    select(ScreeningFinding)
                    .join(ScreeningRun, ScreeningRun.id == ScreeningFinding.run_id)
                    .where(ScreeningRun.image_id == image_id)
                )
            ).scalars()
        )
        assert len(findings) == 2 and {finding.region for finding in findings} == {"mouth", "eye"}
        assert {finding.label for finding in findings} == (
            {"Visible lip crust", "Visible eye discharge"} if cap == 1 else {"ORF", "PINK_EYE"}
        )


async def test_native_provider_answer_alias_preserves_every_unresolved_observation(
    client: httpx.AsyncClient,
) -> None:
    provider = _RegionalProvider("configured-router", "alias")
    _, image_id, summary = await _regional_cycle(client, provider)
    assert summary.flagged == 1 and provider.calls == 1
    async with get_sessionmaker()() as db:
        findings = list(
            (
                await db.execute(
                    select(ScreeningFinding)
                    .join(ScreeningRun, ScreeningRun.id == ScreeningFinding.run_id)
                    .where(ScreeningRun.image_id == image_id)
                )
            ).scalars()
        )
        assert len(findings) == 2
        assert {(finding.region, finding.label) for finding in findings} == {
            ("mouth", "Visible lip crust"),
            ("eye", "Visible eye discharge"),
        }


async def test_unassessable_real_cross_check_does_not_claim_clinical_agreement(
    client: httpx.AsyncClient,
) -> None:
    provider = _RegionalProvider("quality-primary")
    checker = _RegionalProvider("quality-checker", "quality")
    _, image_id, summary = await _regional_cycle(client, provider, secondary=checker)
    assert summary.flagged == 1 and checker.calls == 1
    async with get_sessionmaker()() as db:
        checks = list(
            (
                await db.execute(
                    select(ScreeningRun).where(
                        ScreeningRun.image_id == image_id, ScreeningRun.stage == "CROSS_CHECK"
                    )
                )
            ).scalars()
        )
        assert len(checks) == 1
        assert checks[0].verdict == "unassessable"
        assert checks[0].detail is not None and checks[0].detail.get("agrees") is False
        assert checks[0].detail.get("quality_problem") is True


async def test_native_retained_multiple_safety_passes_reuse_the_latest_real_gate(
    client: httpx.AsyncClient,
) -> None:
    owner, image_id, _ = await _regional_cycle(client, _RegionalProvider("initial-history"))
    async with get_sessionmaker()() as db:
        image = (
            await db.execute(
                select(ScreeningImage).where(ScreeningImage.id == image_id).with_for_update()
            )
        ).scalar_one()
        farm = await db.get(Farm, int(owner["X-Farm-Id"]))
        assert farm is not None
        provider = _RegionalProvider("retained-native-safety", "healthy")
        for _ in range(2):
            status = await _run_cascade(
                db,
                _cycle_settings(),
                ProviderRotation([provider]),
                image,
                None,
                _jpeg_bytes(160, 80),
                CycleSummary(),
                today(farm.timezone),
                coverage_safety_pass=True,
                sample_healthy=False,
            )
            assert status == "HEALTHY"
        await db.commit()
        try:
            coverage_status = await _prior_coverage_safety_status(db, image)
        except MultipleResultsFound as exc:
            pytest.fail(
                f"Actual retained model history must select one most recent safety pass: {exc}"
            )
        assert coverage_status == "HEALTHY" and provider.calls == 2
