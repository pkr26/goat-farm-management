"""Clinical-evidence and evaluation oracles for workflow mutation campaigns.

Prepared outside the live test tree while its coverage baseline is collected.
The expected image locations, sampling vectors and rounded Wilson endpoints
are fixed independently of the application implementation.
"""

from __future__ import annotations

import hashlib
import io
from datetime import date
from decimal import Decimal

import httpx
import pytest
from PIL import Image

from app.api.screening import _wilson_interval
from app.db import get_sessionmaker
from app.models import Farm, ScreeningFinding, ScreeningImage, ScreeningRun
from app.models.screening import HEALTHY_CONTROL_LABEL
from app.services.screening.images import (
    CropError,
    ImageNormalizationError,
    crop_image,
    normalize_image,
)
from app.services.screening.pipeline import _sample_healthy_control
from app.services.screening.rotation import ProviderRotation
from app.utils import utcnow

from .conftest import owner_with_farm
from .test_screening import CountingProvider, _jpeg_bytes


def _assert_color(actual: float | tuple[int, ...] | None, expected: tuple[int, int, int]) -> None:
    # JPEG encoding perturbs edge pixels. Probe broad solid regions and allow
    # codec rounding without making a red/blue or quadrant swap acceptable.
    assert isinstance(actual, tuple) and len(actual) == 3
    assert all(abs(actual[index] - expected[index]) <= 20 for index in range(3))


@pytest.mark.parametrize(
    ("orientation", "top", "bottom"),
    [
        (6, (255, 0, 0), (0, 0, 255)),
        (8, (0, 0, 255), (255, 0, 0)),
    ],
)
def test_normalization_applies_exif_orientation_before_stripping_evidence(
    orientation: int, top: tuple[int, int, int], bottom: tuple[int, int, int]
) -> None:
    photo = Image.new("RGB", (240, 120), (255, 0, 0))
    photo.paste((0, 0, 255), (120, 0, 240, 120))
    exif = Image.Exif()
    exif[0x0112] = orientation
    exif[0x010F] = "Clinical test camera"
    source = io.BytesIO()
    photo.save(source, format="JPEG", quality=95, exif=exif)

    try:
        normalized = normalize_image(source.getvalue(), 512, "image/jpeg")
    except ImageNormalizationError as exc:
        pytest.fail(f"A supported, valid camera JPEG must normalize successfully: {exc}")

    assert (normalized.width, normalized.height) == (120, 240)
    assert normalized.sha256 == hashlib.sha256(normalized.data).hexdigest()
    assert normalized.sha256 != hashlib.sha256(source.getvalue()).hexdigest()
    assert normalized.byte_size == len(normalized.data)
    assert b"Exif" not in normalized.data
    with Image.open(io.BytesIO(normalized.data)) as evidence:
        assert evidence.size == (120, 240)
        assert not evidence.getexif()
        _assert_color(evidence.getpixel((60, 40)), top)
        _assert_color(evidence.getpixel((60, 200)), bottom)


def test_png_normalization_preserves_supported_pixels_and_hashes_output_bytes() -> None:
    photo = Image.new("RGB", (180, 90), (0, 180, 30))
    photo.paste((220, 0, 180), (90, 0, 180, 90))
    source = io.BytesIO()
    photo.save(source, format="PNG")

    try:
        normalized = normalize_image(source.getvalue(), 512, "image/png")
    except ImageNormalizationError as exc:
        pytest.fail(f"A supported, valid PNG must normalize successfully: {exc}")

    assert (normalized.width, normalized.height) == (180, 90)
    assert normalized.sha256 == hashlib.sha256(normalized.data).hexdigest()
    assert normalized.sha256 != hashlib.sha256(source.getvalue()).hexdigest()
    with Image.open(io.BytesIO(normalized.data)) as evidence:
        assert evidence.format == "JPEG"
        _assert_color(evidence.getpixel((30, 45)), (0, 180, 30))
        _assert_color(evidence.getpixel((150, 45)), (220, 0, 180))


def test_rectangular_crop_retains_the_declared_spatial_regions_and_margin() -> None:
    photo = Image.new("RGB", (400, 200), (255, 0, 0))
    photo.paste((0, 255, 0), (200, 0, 400, 100))
    photo.paste((0, 0, 255), (0, 100, 200, 200))
    photo.paste((255, 255, 0), (200, 100, 400, 200))
    source = io.BytesIO()
    photo.save(source, format="JPEG", quality=95)

    # The declared box spans x=80..240 and y=20..160. The five-percent
    # full-frame margin extends it to x=60..260 and y=10..170.
    try:
        crop = crop_image(source.getvalue(), (200, 100, 400, 700))
    except CropError as exc:
        pytest.fail(f"A usable valid detection box must retain its clinical evidence: {exc}")

    assert (crop.width, crop.height) == (200, 160)
    assert crop.sha256 == hashlib.sha256(crop.data).hexdigest()
    with Image.open(io.BytesIO(crop.data)) as evidence:
        assert evidence.size == (200, 160)
        _assert_color(evidence.getpixel((20, 20)), (255, 0, 0))
        _assert_color(evidence.getpixel((180, 20)), (0, 255, 0))
        _assert_color(evidence.getpixel((20, 140)), (0, 0, 255))
        _assert_color(evidence.getpixel((180, 140)), (255, 255, 0))


async def test_three_provider_fallback_retains_an_independent_second_opinion() -> None:
    # 2026-10-07's ordinal is divisible by both three and four: provider A
    # is the declared primary in the fixed rotations used by these tests.
    on = date(2026, 10, 7)
    primary = CountingProvider(name="a", fail=True)
    serving = CountingProvider(name="b")
    second_opinion = CountingProvider(name="c")
    rotation = ProviderRotation([primary, serving, second_opinion])

    outcome = await rotation.gate_with_fallback(primary, _jpeg_bytes(120, 60))

    assert outcome.served_by == "b"
    assert outcome.failed_providers == ("a",)
    assert primary.calls == serving.calls == 1
    assert second_opinion.calls == 0
    checker = rotation.cross_checker_for(on, outcome.served_by, outcome.failed_providers)
    assert checker is second_opinion
    assert checker is not serving


def test_cross_checker_excludes_every_failed_provider_and_the_serving_provider() -> None:
    a, b, c, d = [CountingProvider(name=name) for name in "abcd"]
    rotation = ProviderRotation([a, b, c, d])
    on = date(2026, 10, 7)

    assert rotation.cross_checker_for(on, "c", ("a", "b")) is d
    assert rotation.cross_checker_for(on, "c", ("a", "b", "d")) is None
    assert ProviderRotation([a]).cross_checker_for(on, "a") is None


@pytest.mark.parametrize(
    ("successes", "total", "low", "high"),
    [
        (0, 1, "0.000", "0.793"),
        (1, 1, "0.207", "1.000"),
        (1, 2, "0.095", "0.905"),
        (2, 3, "0.208", "0.939"),
        (0, 2, "0.000", "0.658"),
        (2, 10, "0.057", "0.510"),
        (5, 10, "0.237", "0.763"),
        (100, 200, "0.431", "0.569"),
    ],
)
def test_wilson_interval_matches_independent_95_percent_endpoints(
    successes: int, total: int, low: str, high: str
) -> None:
    # Fixed rounded reference endpoints, including asymmetric, zero-event,
    # all-event and large-cohort cases; the expected values do not call or
    # duplicate the application's interval arithmetic.
    try:
        observed = _wilson_interval(successes, total)
    except (ArithmeticError, TypeError, ValueError) as exc:
        pytest.fail(f"Valid binomial counts must yield finite Wilson endpoints: {exc}")
    assert observed == (Decimal(low), Decimal(high))


def test_wilson_interval_has_no_estimate_without_reviewed_observations() -> None:
    try:
        observed = _wilson_interval(0, 0)
    except (ArithmeticError, TypeError, ValueError) as exc:
        pytest.fail(f"An empty reviewed cohort must have no estimate: {exc}")
    assert observed is None


@pytest.mark.parametrize(
    ("farm_id", "digest_suffix", "sampled"),
    [
        (1, "0004", True),
        (2, "0004", False),
        (1, "0010", False),
        (2, "0010", True),
        (1, "0000", False),
        (2, "0000", False),
    ],
)
def test_healthy_control_sample_matches_fixed_farm_local_hash_vectors(
    farm_id: int, digest_suffix: str, sampled: bool
) -> None:
    # These vectors were computed independently with SHA-256 over the
    # versioned clinical sample namespace. Image identity may differ for an
    # identical reupload without changing the stable content sample.
    for image_id in (101, 202):
        image = ScreeningImage(
            id=image_id,
            farm_id=farm_id,
            s3_bucket="sample-fixture",
            s3_key=f"raw/{farm_id}/sample-{image_id}.jpg",
            sha256=digest_suffix.zfill(64),
            status="HEALTHY",
        )
        assert _sample_healthy_control(image) is sampled


@pytest.mark.parametrize(
    ("farm_id", "image_id", "sampled"),
    [(1, 2, True), (2, 2, False), (1, 5, False), (2, 5, True)],
)
def test_legacy_healthy_sample_uses_image_identity_when_digest_is_absent(
    farm_id: int, image_id: int, sampled: bool
) -> None:
    image = ScreeningImage(
        id=image_id,
        farm_id=farm_id,
        s3_bucket="sample-fixture",
        s3_key=f"raw/{farm_id}/sample-{image_id}.jpg",
        status="HEALTHY",
    )
    assert _sample_healthy_control(image) is sampled


async def test_provider_quality_separates_models_reviewed_positives_and_healthy_controls(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="mutation38-quality@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    # Same provider name, three different models. A reviewed healthy control
    # is a different denominator from the model's emitted positive findings.
    cohorts = [
        (
            "clinical-v1",
            ["CONFIRMED", "CONFIRMED", "REJECTED", "PENDING_REVIEW", "PENDING_REVIEW"],
            ["CONFIRMED", "REJECTED", "REJECTED", "PENDING_REVIEW"],
        ),
        ("clinical-v2", ["REJECTED", "PENDING_REVIEW"], ["CONFIRMED", "CONFIRMED"]),
        ("clinical-v3", ["PENDING_REVIEW"], ["PENDING_REVIEW"]),
    ]
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        reviewer_id = farm.owner_id
        reviewed_at = utcnow()
        for model, positives, controls in cohorts:
            for kind, statuses in [("positive", positives), ("control", controls)]:
                image = ScreeningImage(
                    farm_id=farm_id,
                    s3_bucket="quality-fixture",
                    s3_key=f"raw/{farm_id}/{model}-{kind}.jpg",
                    normalized_key=f"screening/{farm_id}/{model}-{kind}.jpg",
                    status="FLAGGED" if kind == "positive" else "HEALTHY",
                )
                db.add(image)
                await db.flush()
                run = ScreeningRun(
                    farm_id=farm_id,
                    image_id=image.id,
                    stage="GATE",
                    run_status="OK",
                    verdict="flagged" if kind == "positive" else "healthy",
                    confidence=Decimal("0.800"),
                    provider="same-provider",
                    model=model,
                    prompt_version="gate-quality-fixture",
                    latency_ms=10,
                )
                db.add(run)
                await db.flush()
                for index, status in enumerate(statuses):
                    is_reviewed = status != "PENDING_REVIEW"
                    db.add(
                        ScreeningFinding(
                            farm_id=farm_id,
                            run_id=run.id,
                            label=(
                                HEALTHY_CONTROL_LABEL if kind == "control" else f"WOUND-{index}"
                            ),
                            confidence=Decimal("0.800"),
                            status=status,
                            reviewed_by_id=reviewer_id if is_reviewed else None,
                            reviewed_at=reviewed_at if is_reviewed else None,
                        )
                    )
        await db.commit()

    response = await client.get("/api/screening/stats", headers=owner)
    assert response.status_code == 200, response.text
    providers = response.json()["providers"]
    assert len(providers) == 3
    assert {row["provider"] for row in providers} == {"same-provider"}
    by_model = {row["model"]: row for row in providers}
    assert set(by_model) == {"clinical-v1", "clinical-v2", "clinical-v3"}

    first = by_model["clinical-v1"]
    assert first["gate_runs"] == 2 and first["gate_flagged"] == 1
    assert (first["findings_confirmed"], first["findings_rejected"], first["findings_pending"]) == (
        2,
        1,
        2,
    )
    assert first["positive_precision"] == "0.667"
    assert (first["positive_precision_ci_low"], first["positive_precision_ci_high"]) == (
        "0.208",
        "0.939",
    )
    assert (
        first["healthy_controls_confirmed"],
        first["healthy_controls_rejected"],
        first["healthy_controls_pending"],
        first["healthy_controls_reviewed"],
    ) == (1, 2, 1, 3)
    assert first["healthy_false_negative_rate"] == "0.667"
    assert (first["healthy_false_negative_ci_low"], first["healthy_false_negative_ci_high"]) == (
        "0.208",
        "0.939",
    )

    second = by_model["clinical-v2"]
    assert second["positive_precision"] == "0.000"
    assert (second["positive_precision_ci_low"], second["positive_precision_ci_high"]) == (
        "0.000",
        "0.793",
    )
    assert second["healthy_controls_reviewed"] == 2
    assert second["healthy_false_negative_rate"] == "0.000"
    assert (second["healthy_false_negative_ci_low"], second["healthy_false_negative_ci_high"]) == (
        "0.000",
        "0.658",
    )

    pending = by_model["clinical-v3"]
    assert pending["findings_pending"] == pending["healthy_controls_pending"] == 1
    assert pending["healthy_controls_reviewed"] == 0
    for metric in [
        "positive_precision",
        "positive_precision_ci_low",
        "positive_precision_ci_high",
        "healthy_false_negative_rate",
        "healthy_false_negative_ci_low",
        "healthy_false_negative_ci_high",
    ]:
        assert pending[metric] is None
