"""Disease-screening pipeline tests.

The unit section (gate/specialist contracts, key parsing, normalization,
rotation resolver, provider adapters against a mocked transport) needs no
database and runs everywhere. The integration section drives the full
cascade and the review API through the same real-PostgreSQL fixtures as
the rest of the suite.
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import io
import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from app.core.config import ScreeningRotationProvider, Settings
from app.db import get_sessionmaker
from app.models import ScreeningCrop, ScreeningFinding, ScreeningImage, ScreeningRun
from app.services.screening.detect import (
    DetectionBox,
    DetectionParseError,
    parse_detection_response,
)
from app.services.screening.gate import (
    GateParseError,
    parse_gate_response,
)
from app.services.screening.images import (
    CropError,
    ImageNormalizationError,
    crop_image,
    normalize_image,
)
from app.services.screening.pipeline import parse_raw_key, run_screening_cycle
from app.services.screening.providers import (
    AnthropicProvider,
    GateCallResult,
    OpenAICompatibleProvider,
    ProviderAnswer,
    ProviderError,
)
from app.services.screening.providers import (
    gate as run_gate,
)
from app.services.screening.rotation import GateExhaustedError, ProviderRotation
from app.services.screening.s3 import ScreeningObjectMissingError, ScreeningStorage
from app.services.screening.specialists import (
    SpecialistKind,
    SpecialistParseError,
    parse_specialist_response,
    specialist_for_region,
)

from .conftest import owner_with_farm

# --------------------------------------------------------------------------
# Unit: gate JSON contract
# --------------------------------------------------------------------------

HEALTHY_ANSWER = json.dumps(
    {"flagged": False, "confidence": 0.93, "quality_problem": False, "observations": []}
)

FLAGGED_ANSWER = json.dumps(
    {
        "flagged": True,
        "confidence": 0.71,
        "quality_problem": False,
        "observations": [
            {
                "region": "mouth",
                "label": "crusty scabs near lips",
                "confidence": 0.66,
                "note": "raised crusty lesions consistent with orf",
            }
        ],
    }
)


def test_gate_parse_plain_json() -> None:
    parsed = parse_gate_response(FLAGGED_ANSWER)
    assert parsed.flagged is True
    assert parsed.confidence == pytest.approx(0.71)
    assert parsed.observations[0].region == "mouth"


def test_gate_parse_strips_markdown_fences_and_prose() -> None:
    chatty = f"Here is my assessment:\n```json\n{FLAGGED_ANSWER}\n```\nHope this helps."
    assert parse_gate_response(chatty).flagged is True


def test_gate_parse_healthy_drops_stray_observations() -> None:
    # A "healthy" verdict with observations would deadlock the review queue;
    # the contract normalizes instead of rejecting.
    contradictory = json.dumps(
        {
            "flagged": False,
            "confidence": 0.8,
            "quality_problem": False,
            "observations": [
                {"region": "eye", "label": "something", "confidence": 0.5, "note": "n"}
            ],
        }
    )
    assert parse_gate_response(contradictory).observations == []


def test_gate_parse_garbage_raises() -> None:
    with pytest.raises(GateParseError):
        parse_gate_response("no json here at all")


def test_gate_parse_out_of_range_confidence_rejected() -> None:
    with pytest.raises(GateParseError):
        parse_gate_response(json.dumps({"flagged": True, "confidence": 7.5}))


# --------------------------------------------------------------------------
# Unit: specialist contract
# --------------------------------------------------------------------------

SKIN_ANSWER = json.dumps(
    {
        "conditions": [
            {
                "disease": "ORF",
                "confidence": 0.72,
                "severity": "moderate",
                "note": "raised crusty lesions on the lips",
            }
        ]
    }
)


def test_specialist_parse_valid_answer() -> None:
    parsed = parse_specialist_response(SKIN_ANSWER, SpecialistKind.SKIN)
    assert parsed.conditions[0].disease == "ORF"
    assert parsed.conditions[0].severity == "moderate"


def test_specialist_parse_coerces_unknown_disease_to_other() -> None:
    # Unknown codes are kept (as OTHER, original guess preserved in the
    # note) rather than silently renamed or dropped.
    chatty = json.dumps(
        {
            "conditions": [
                {
                    "disease": "BLUE_TONGUE_SUSPECT",
                    "confidence": 0.5,
                    "severity": "mild",
                    "note": "swollen face",
                }
            ]
        }
    )
    parsed = parse_specialist_response(chatty, SpecialistKind.SKIN)
    assert parsed.conditions[0].disease == "OTHER"
    assert "BLUE_TONGUE_SUSPECT" in (parsed.conditions[0].note or "")


def test_specialist_parse_drops_malformed_conditions() -> None:
    mixed = json.dumps(
        {
            "conditions": [
                {"disease": "MANGE", "confidence": 0.6, "severity": "severe"},
                {"disease": "NOT_A_REAL_COLUMN", "confidence": 9, "severity": "nope"},
            ]
        }
    )
    parsed = parse_specialist_response(mixed, SpecialistKind.SKIN)
    assert [condition.disease for condition in parsed.conditions] == ["MANGE"]


def test_specialist_parse_garbage_raises() -> None:
    with pytest.raises(SpecialistParseError):
        parse_specialist_response("no json at all", SpecialistKind.EYE)


@pytest.mark.parametrize(
    ("region", "expected"),
    [
        ("mouth", SpecialistKind.SKIN),
        ("MOUTH", SpecialistKind.SKIN),
        ("eye", SpecialistKind.EYE),
        ("hoof", SpecialistKind.HOOF),
        ("legs", SpecialistKind.HOOF),
        ("udder", SpecialistKind.UDDER),
        ("general", SpecialistKind.GENERAL),
        (None, SpecialistKind.GENERAL),
        ("tail-fluff", SpecialistKind.GENERAL),
    ],
)
def test_specialist_for_region(region: str | None, expected: SpecialistKind) -> None:
    assert specialist_for_region(region) is expected


# --------------------------------------------------------------------------
# Unit: rotation resolver
# --------------------------------------------------------------------------


@dataclass
class CountingProvider:
    """Fake vision provider: gate answers keyed by image aspect ratio,
    specialist answers keyed by prompt type, detection answers from a
    settable box list; counts every call."""

    name: str
    model: str = "fake-gate-1"
    fail: bool = False
    calls: int = field(default=0)
    detect_boxes: list[list[int]] = field(default_factory=lambda: [[80, 80, 840, 840]])

    async def complete(self, image_jpeg: bytes, system_prompt: str) -> ProviderAnswer:
        self.calls += 1
        if self.fail:
            raise ProviderError(f"{self.name} outage")
        with Image.open(io.BytesIO(image_jpeg)) as decoded:
            landscape = decoded.width > decoded.height
        if "Find every goat" in system_prompt:
            text = json.dumps({"goats": [{"box": box} for box in self.detect_boxes]})
        elif "veterinary specialist" in system_prompt:
            if "skin, lips" in system_prompt:
                text = SKIN_ANSWER
            else:
                text = json.dumps({"conditions": []})
        else:
            text = FLAGGED_ANSWER if landscape else HEALTHY_ANSWER
        return ProviderAnswer(text=text, provider=self.name, model=self.model, latency_ms=1)


def test_rotation_primary_is_deterministic_and_wraps() -> None:
    a, b = CountingProvider(name="a"), CountingProvider(name="b")
    rotation = ProviderRotation([a, b])
    day1 = rotation.primary_for(dt.date(2026, 9, 14))
    # Same date → same provider, every call, no stored state.
    assert rotation.primary_for(dt.date(2026, 9, 14)) is day1
    # Consecutive ordinals alternate — Monday/Tuesday round-robin.
    ordinals = [dt.date(2026, 9, day).toordinal() for day in range(14, 18)]
    primaries = [rotation.primary_for(dt.date.fromordinal(o)).name for o in ordinals]
    assert primaries[0] != primaries[1]
    assert primaries[0] == primaries[2]


def test_rotation_secondary_follows_primary() -> None:
    a, b, c = (CountingProvider(name=n) for n in "abc")
    rotation = ProviderRotation([a, b, c])
    primary = rotation.primary_for(dt.date(2026, 9, 17))
    secondary = rotation.secondary_for(dt.date(2026, 9, 17))
    assert secondary is not None
    assert secondary is not primary
    assert secondary is rotation.primary_for(dt.date(2026, 9, 18))


def test_rotation_single_provider_has_no_secondary() -> None:
    rotation = ProviderRotation([CountingProvider(name="solo")])
    assert rotation.secondary_for(dt.date(2026, 9, 17)) is None


def test_rotation_rejects_empty() -> None:
    with pytest.raises(ValueError):
        ProviderRotation([])


def test_rotation_gate_falls_back_on_primary_failure() -> None:
    failing = CountingProvider(name="primary-down", fail=True)
    healthy_provider = CountingProvider(name="backup")
    rotation = ProviderRotation([failing, healthy_provider])

    async def call() -> Any:
        return await rotation.gate_with_fallback(failing, _jpeg_bytes(100, 50))

    outcome = asyncio.run(call())
    assert outcome.served_by == "backup"
    assert outcome.failed_providers == ("primary-down",)
    assert outcome.result.response.flagged is True  # landscape


def test_rotation_gate_exhausted_when_all_fail() -> None:
    providers = [CountingProvider(name="a", fail=True), CountingProvider(name="b", fail=True)]
    rotation = ProviderRotation(providers)

    async def call() -> Any:
        return await rotation.gate_with_fallback(providers[0], _jpeg_bytes(100, 50))

    with pytest.raises(GateExhaustedError) as excinfo:
        asyncio.run(call())
    assert set(excinfo.value.failed_providers) == {"a", "b"}


# --------------------------------------------------------------------------
# Unit: raw key parsing
# --------------------------------------------------------------------------


def test_parse_raw_key_happy_path() -> None:
    parsed = parse_raw_key("raw/42/2026-09-17/herd_photo_001.jpg", "raw")
    assert parsed is not None
    assert parsed.farm_id == 42
    assert parsed.captured_date == dt.date(2026, 9, 17)
    assert parsed.bucket is None


def test_parse_raw_key_with_bucket_segment() -> None:
    parsed = parse_raw_key("raw/42/2026-09-17/QUARANTINE/arrival.jpg", "raw")
    assert parsed is not None
    assert parsed.bucket == "QUARANTINE"


def test_parse_raw_key_rejects_unknown_bucket_code() -> None:
    # A bucket-shaped segment that is not a herd bucket code is a misplaced
    # key, not a whole-farm photo with a slashed filename.
    assert parse_raw_key("raw/42/2026-09-17/GARBAGE/x.jpg", "raw") is None


@pytest.mark.parametrize(
    "key",
    [
        "raw/42/not-a-date/photo.jpg",  # malformed date
        "raw/abc/2026-09-17/photo.jpg",  # farm id not numeric
        "raw/2026-09-17/photo.jpg",  # missing farm segment
        "other/42/2026-09-17/photo.jpg",  # wrong prefix
        "raw/42/2026-09-17/",  # empty filename
        "raw/42/2026-13-45/photo.jpg",  # impossible date
    ],
)
def test_parse_raw_key_rejects_malformed(key: str) -> None:
    assert parse_raw_key(key, "raw") is None


# --------------------------------------------------------------------------
# Unit: image normalization
# --------------------------------------------------------------------------


def _jpeg_bytes(width: int, height: int) -> bytes:
    image = Image.new("RGB", (width, height), color=(120, 80, 40))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


def test_normalize_downscales_and_hashes() -> None:
    # A 6000x4000 (24 MP) "HD" photo must come back at most 1568 on the
    # long edge, with a hash that is stable across runs.
    big = _jpeg_bytes(6000, 4000)
    normalized = normalize_image(big, max_edge=1568)
    assert max(normalized.width, normalized.height) == 1568
    assert normalized.byte_size < len(big)
    again = normalize_image(big, max_edge=1568)
    assert again.sha256 == normalized.sha256


def test_normalize_small_image_kept_without_upscaling() -> None:
    small = _jpeg_bytes(640, 480)
    normalized = normalize_image(small, max_edge=1568)
    assert (normalized.width, normalized.height) == (640, 480)


def test_normalize_strips_exif_metadata() -> None:
    # Assert on the encoded bytes: an EXIF-free JPEG carries no APP1 "Exif"
    # segment at all, so GPS/camera metadata cannot survive re-encoding.
    source = Image.new("RGB", (800, 600))
    exif = Image.Exif()
    exif[0x010F] = "TestCam"  # Make — any tag proves the EXIF block strips
    buffer = io.BytesIO()
    source.save(buffer, format="JPEG", exif=exif)
    assert b"Exif" in buffer.getvalue()
    normalized = normalize_image(buffer.getvalue(), max_edge=1568)
    assert b"Exif" not in normalized.data


def test_normalize_rejects_non_image() -> None:
    with pytest.raises(ImageNormalizationError):
        normalize_image(b"definitely not a jpeg", max_edge=1568)


# --------------------------------------------------------------------------
# Unit: provider adapters against a mocked transport
# --------------------------------------------------------------------------


def _provider_settings() -> Settings:
    return Settings(
        environment="development",
        screening_enabled=True,
        s3_bucket="goat-photos",
        s3_access_key_id="test-access",
        s3_secret_access_key="test-secret",
        screening_provider="anthropic",
        screening_anthropic_api_key="test-anthropic-key",
        screening_openai_api_key="test-openai-key",
    )


def _anthropic_payload(answer: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": answer}]}


def _openai_payload(answer: str) -> dict[str, Any]:
    return {"choices": [{"message": {"content": answer}}]}


def test_anthropic_provider_sends_image_and_parses_answer() -> None:
    # asyncio.run (not an async test) so the provider contract is verified
    # even under --mutation-pure runs without PostgreSQL.
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("x-api-key")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_anthropic_payload(FLAGGED_ANSWER))

    async def call() -> GateCallResult:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = AnthropicProvider(_provider_settings(), client=client)
        try:
            return await run_gate(provider, _jpeg_bytes(100, 100))
        finally:
            await client.aclose()

    result = asyncio.run(call())

    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["auth"] == "test-anthropic-key"
    body = captured["body"]
    assert body["model"] == "claude-sonnet-4-5"
    image_block = body["messages"][0]["content"][0]["source"]
    assert image_block["media_type"] == "image/jpeg"
    # The image really is our JPEG, base64-encoded.
    assert base64.b64decode(image_block["data"])[:2] == b"\xff\xd8"
    assert result.response.flagged is True
    assert result.provider == "anthropic"
    assert result.latency_ms >= 0


def test_openai_compatible_provider_contract() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_openai_payload(HEALTHY_ANSWER))

    async def call() -> GateCallResult:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = OpenAICompatibleProvider(_provider_settings(), client=client)
        try:
            return await run_gate(provider, _jpeg_bytes(100, 100))
        finally:
            await client.aclose()

    result = asyncio.run(call())

    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["auth"] == "Bearer test-openai-key"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert result.response.flagged is False
    assert result.provider == "openai_compatible"


def test_provider_http_failure_raises_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "overloaded"})

    async def call() -> GateCallResult:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = AnthropicProvider(_provider_settings(), client=client)
        try:
            return await run_gate(provider, _jpeg_bytes(100, 100))
        finally:
            await client.aclose()

    with pytest.raises(ProviderError):
        asyncio.run(call())


# --------------------------------------------------------------------------
# Unit: settings (rotation parsing + fail-closed checks)
# --------------------------------------------------------------------------


def _rotation_entries() -> list[ScreeningRotationProvider]:
    return [
        ScreeningRotationProvider(
            kind="anthropic", name="claude", model="claude-sonnet-4-5", api_key="k1"
        ),
        ScreeningRotationProvider(
            kind="openai_compatible",
            name="glm",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            model="glm-4.6v",
            api_key="k2",
        ),
    ]


def test_rotation_settings_parse_and_enable() -> None:
    settings = Settings(
        environment="development",
        screening_enabled=True,
        s3_bucket="goat-photos",
        s3_access_key_id="a",
        s3_secret_access_key="b",
        screening_provider_rotation=_rotation_entries(),
    )
    assert [entry.name for entry in settings.screening_provider_rotation] == ["claude", "glm"]


def test_rotation_settings_reject_duplicate_names() -> None:
    entries = [
        ScreeningRotationProvider(kind="anthropic", name="x", model="m", api_key="k"),
        ScreeningRotationProvider(kind="openai_compatible", name="x", model="m", api_key="k"),
    ]
    with pytest.raises(ValueError, match="duplicate provider names"):
        Settings(
            environment="development",
            screening_enabled=True,
            s3_bucket="goat-photos",
            s3_access_key_id="a",
            s3_secret_access_key="b",
            screening_provider_rotation=entries,
        )


def test_rotation_settings_reject_blank_api_key() -> None:
    entries = [ScreeningRotationProvider(kind="anthropic", name="x", model="m", api_key=" ")]
    with pytest.raises(ValueError, match="blank api_key"):
        Settings(
            environment="development",
            screening_enabled=True,
            s3_bucket="goat-photos",
            s3_access_key_id="a",
            s3_secret_access_key="b",
            screening_provider_rotation=entries,
        )


def test_rotation_provider_name_must_be_slug() -> None:
    with pytest.raises(ValueError):
        ScreeningRotationProvider(kind="anthropic", name="Not A Slug!", model="m", api_key="k")


def test_presign_put_mints_direct_upload_url() -> None:
    storage = ScreeningStorage(_cycle_settings())
    url = storage.presign_put("raw/1/2026-09-17/BREEDING/7-abc.jpg", "image/jpeg")
    # SigV4 signing is local; the URL encodes the method, key and signed
    # content type the phone must echo on its PUT.
    assert url.startswith("https://")
    assert "raw/1/2026-09-17/BREEDING/7-abc.jpg" in url
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in url
    # The signed headers include content-type: the phone's PUT must send
    # exactly the declared type or S3 rejects the signature.
    assert "content-type" in url


def test_screening_settings_require_full_config_when_enabled() -> None:
    # The fail-closed gate: enabling screening without credentials must
    # refuse to construct Settings at all.
    with pytest.raises(ValueError, match="incomplete screening config"):
        Settings(environment="development", screening_enabled=True)


def test_screening_settings_reject_plain_http_provider_url() -> None:
    with pytest.raises(ValueError, match="https"):
        Settings(
            environment="development",
            screening_anthropic_base_url="http://api.example.com",
        )


# --------------------------------------------------------------------------
# Unit: detection contract + cropper
# --------------------------------------------------------------------------

TWO_GOAT_ANSWER = json.dumps(
    {"goats": [{"box": [100, 100, 600, 300]}, {"box": [700, 100, 250, 600]}]}
)


def test_detection_parse_two_goats() -> None:
    boxes = parse_detection_response(TWO_GOAT_ANSWER, max_goats=8)
    assert [(b.x, b.y, b.w, b.h) for b in boxes] == [(100, 100, 600, 300), (700, 100, 250, 600)]


def test_detection_parse_strips_fences_and_prose() -> None:
    chatty = f"Sure!\n```json\n{TWO_GOAT_ANSWER}\n```"
    assert len(parse_detection_response(chatty, max_goats=8)) == 2


def test_detection_parse_drops_degenerate_and_caps_count() -> None:
    noisy = json.dumps(
        {
            "goats": [
                {"box": [10, 10, 5, 5]},  # whisker sliver → noise
                {"box": [0, 0, 500, 500]},
                {"box": [500, 0, 500, 500]},
                {"box": [0, 500, 500, 500]},
            ]
        }
    )
    assert len(parse_detection_response(noisy, max_goats=2)) == 2  # capped


def test_detection_parse_clamps_out_of_frame_boxes() -> None:
    sloppy = json.dumps({"goats": [{"box": [-50, -10, 900, 1200]}]})
    (box,) = parse_detection_response(sloppy, max_goats=4)
    assert (box.x, box.y, box.w, box.h) == (0, 0, 900, 1000)


def test_detection_parse_garbage_raises() -> None:
    with pytest.raises(DetectionParseError):
        parse_detection_response("no json", max_goats=4)


def test_crop_image_cuts_the_box_with_margin() -> None:
    source = _jpeg_bytes(1000, 500)
    cropped = crop_image(source, (500, 250, 300, 250))
    # x: 0.5*1000 - 50 = 450 .. 0.8*1000 + 50 = 850 → 400 wide
    # y: 0.25*500 - 25 = 100 .. 0.5*500 + 25 = 275 → 175 tall
    assert (cropped.width, cropped.height) == (400, 175)
    assert cropped.sha256


def test_crop_image_clamps_at_frame_edges() -> None:
    source = _jpeg_bytes(1000, 500)
    cropped = crop_image(source, (0, 0, 100, 100))
    assert cropped.width <= 100 + 100  # 10% + margin, but never past 0..1000
    assert cropped.height <= 50 + 50
    assert cropped.width > 0 and cropped.height > 0


def test_crop_image_degenerate_box_raises() -> None:
    # Margin is 5% of a tiny frame, so a near-corner sliver collapses
    # below the 8px minimum and must raise rather than send an empty image.
    source = _jpeg_bytes(100, 60)
    with pytest.raises(CropError):
        crop_image(source, (990, 990, 10, 10))


def test_detection_box_clamped_helper() -> None:
    assert DetectionBox(x=-5, y=1200, w=0, h=2000).clamped() == DetectionBox(0, 1000, 1, 1000)


# --------------------------------------------------------------------------
# Integration: full cascade + review API (real PostgreSQL, like the suite)
# --------------------------------------------------------------------------


@dataclass
class FakeStorage:
    """In-memory stand-in for ScreeningStorage: no network, per-test."""

    objects: dict[str, bytes] = field(default_factory=dict)
    uploaded: dict[str, bytes] = field(default_factory=dict)

    @property
    def bucket(self) -> str:
        return "goat-photos"

    def list_object_keys(self, prefix: str, max_keys: int) -> list[str]:
        return sorted(k for k in self.objects if k.startswith(prefix + "/"))[:max_keys]

    def download(self, key: str) -> bytes:
        if key not in self.objects:
            # Mirrors the real client's NoSuchKey → ScreeningObjectMissingError.
            raise ScreeningObjectMissingError(f"object not in bucket yet: {key!r}")
        return self.objects[key]

    def upload(self, key: str, data: bytes, content_type: str) -> None:
        self.uploaded[key] = data

    def presign_get(self, key: str) -> str:
        return f"https://fake-local/{self.bucket}/{key}"


def _cycle_settings(**kwargs: bool) -> Settings:
    return Settings(
        environment="development",
        screening_enabled=True,
        s3_bucket="goat-photos",
        s3_access_key_id="test-access",
        s3_secret_access_key="test-secret",
        screening_provider="anthropic",
        screening_anthropic_api_key="test-key",
        screening_max_images_per_cycle=10,
        screening_crop_detection_enabled=kwargs.get("crop_detection", False),
    )


async def test_full_cycle_screens_flags_and_dedupes(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client, email="screening-owner@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    today = dt.date.today().isoformat()

    storage = FakeStorage()
    storage.objects[f"raw/{farm_id}/{today}/herd_wide.jpg"] = _jpeg_bytes(2000, 1000)
    storage.objects[f"raw/{farm_id}/{today}/pen_tall.jpg"] = _jpeg_bytes(1000, 2000)
    storage.objects[f"raw/{farm_id}/misplaced.txt"] = b"no date segment at all"
    storage.objects["raw/999999/2026-09-17/unknown_farm.jpg"] = _jpeg_bytes(300, 300)

    provider = CountingProvider(name="fake")
    rotation = ProviderRotation([provider])
    async with get_sessionmaker()() as db:
        summary = await run_screening_cycle(db, _cycle_settings(), storage, rotation)
        images = list(
            (await db.execute(select(ScreeningImage).order_by(ScreeningImage.s3_key))).scalars()
        )

    assert (summary.healthy, summary.flagged) == (1, 1)
    assert summary.unparseable_keys == 1
    assert summary.unknown_farm_keys == 1
    # Only well-formed keys under a known farm become rows.
    assert len(images) == 2
    by_key = {image.s3_key: image for image in images}
    wide = by_key[f"raw/{farm_id}/{today}/herd_wide.jpg"]
    tall = by_key[f"raw/{farm_id}/{today}/pen_tall.jpg"]
    assert wide.status == "FLAGGED"
    assert tall.status == "HEALTHY"
    assert wide.normalized_key and wide.normalized_key in storage.uploaded
    assert wide.sha256 != tall.sha256

    async with get_sessionmaker()() as db:
        findings = list((await db.execute(select(ScreeningFinding))).scalars())
        runs = list((await db.execute(select(ScreeningRun))).scalars())
    assert {run.run_status for run in runs} == {"OK"}
    assert all(run.provider == "fake" for run in runs)
    # The cascade: tall photo = gate only; wide photo = gate + skin
    # specialist (its mouth observation maps to SKIN) — findings carry the
    # specialist's vocabulary disease, not the gate's free-text label.
    assert provider.calls == 3
    wide_runs = [run for run in runs if run.image_id == wide.id]
    assert {run.stage for run in wide_runs} == {"GATE", "SPECIALIST_SKIN"}
    assert len(findings) == 1
    assert findings[0].label == "ORF"
    assert findings[0].severity == "moderate"
    assert findings[0].status == "PENDING_REVIEW"
    assert findings[0].region == "mouth"


async def test_rotation_cascade_runs_cross_check_and_specialists(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, email="rotation-owner@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    today = dt.date.today().isoformat()

    storage = FakeStorage()
    storage.objects[f"raw/{farm_id}/{today}/wide.jpg"] = _jpeg_bytes(2000, 1000)
    storage.objects[f"raw/{farm_id}/{today}/tall.jpg"] = _jpeg_bytes(1000, 2000)

    providers = [CountingProvider(name="alpha"), CountingProvider(name="beta")]
    rotation = ProviderRotation(providers)
    async with get_sessionmaker()() as db:
        summary = await run_screening_cycle(db, _cycle_settings(), storage, rotation)

    assert (summary.healthy, summary.flagged) == (1, 1)
    primary = rotation.primary_for(dt.date.today())
    secondary = rotation.secondary_for(dt.date.today())
    assert secondary is not None

    async with get_sessionmaker()() as db:
        runs = list((await db.execute(select(ScreeningRun))).scalars())
    gate_runs = [run for run in runs if run.stage == "GATE"]
    cross_checks = [run for run in runs if run.stage == "CROSS_CHECK"]
    specialists = [run for run in runs if run.stage.startswith("SPECIALIST_")]

    # Every photo got its gate from the day's primary; only the flagged
    # photo earned the specialist + cross-check extra calls.
    assert all(run.provider == primary.name for run in gate_runs)
    assert len(specialists) == 1
    assert specialists[0].provider == primary.name
    assert len(cross_checks) == 1
    assert cross_checks[0].provider == secondary.name
    assert cross_checks[0].verdict == "flagged"  # landscape → agrees
    assert cross_checks[0].detail is not None
    assert cross_checks[0].detail.get("agrees") is True


async def test_rotation_primary_failure_falls_back_mid_cycle(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, email="fallback-owner@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    today = dt.date.today().isoformat()

    storage = FakeStorage()
    storage.objects[f"raw/{farm_id}/{today}/wide.jpg"] = _jpeg_bytes(2000, 1000)

    failing = CountingProvider(name="primary-down", fail=True)
    backup = CountingProvider(name="backup")
    # The failing provider must actually be the day's primary for the
    # fallback path to be exercised deterministically.
    providers = [failing, backup]
    rotation = ProviderRotation(providers)
    if rotation.primary_for(dt.date.today()) is not failing:
        rotation = ProviderRotation([backup, failing])

    async with get_sessionmaker()() as db:
        summary = await run_screening_cycle(db, _cycle_settings(), storage, rotation)

    assert summary.flagged == 1
    async with get_sessionmaker()() as db:
        runs = list((await db.execute(select(ScreeningRun))).scalars())
    gate = next(run for run in runs if run.stage == "GATE")
    assert gate.provider == "backup"
    assert gate.detail is not None
    assert gate.detail.get("fallbacks_failed") == ["primary-down"]


async def test_second_cycle_is_idempotent_and_duplicates_skip(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, email="dedupe-owner@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    today = dt.date.today().isoformat()
    photo = _jpeg_bytes(2000, 1000)

    storage = FakeStorage()
    storage.objects[f"raw/{farm_id}/{today}/a.jpg"] = photo
    rotation = ProviderRotation([CountingProvider(name="fake")])

    async with get_sessionmaker()() as db:
        first = await run_screening_cycle(db, _cycle_settings(), storage, rotation)

    # Same bytes under a NEW key: claimed, recognized as duplicate, skipped
    # instead of being billed to the model again.
    storage.objects[f"raw/{farm_id}/{today}/b_copy.jpg"] = photo
    async with get_sessionmaker()() as db:
        second = await run_screening_cycle(db, _cycle_settings(), storage, rotation)

    assert (first.claimed, first.flagged) == (1, 1)
    assert (second.claimed, second.skipped) == (1, 1)
    assert second.flagged == 0


async def test_provider_failure_records_error_run_and_recovers(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, email="retry-owner@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    today = dt.date.today().isoformat()

    storage = FakeStorage()
    storage.objects[f"raw/{farm_id}/{today}/flaky.jpg"] = _jpeg_bytes(2000, 1000)

    failing = CountingProvider(name="fake", fail=True)
    rotation = ProviderRotation([failing])
    async with get_sessionmaker()() as db:
        first = await run_screening_cycle(db, _cycle_settings(), storage, rotation)

    assert first.errors == 1
    async with get_sessionmaker()() as db:
        run = (await db.execute(select(ScreeningRun))).scalar_one()
        image = (await db.execute(select(ScreeningImage))).scalar_one()
    assert run.run_status == "ERROR"
    assert run.provider == "fake"
    assert image.status == "ERROR"
    assert image.error

    # The ERROR row ages past ERROR_RETRY_AFTER and is re-claimed: the same
    # object screens successfully once the provider is healthy again.
    async with get_sessionmaker()() as db:
        aged_image = (
            await db.execute(select(ScreeningImage).where(ScreeningImage.id == image.id))
        ).scalar_one()
        aged_run = (
            await db.execute(select(ScreeningRun).where(ScreeningRun.id == run.id))
        ).scalar_one()
        aged_image.created_at = aged_image.created_at - dt.timedelta(hours=2)
        aged_run.created_at = aged_run.created_at - dt.timedelta(hours=2)
        await db.commit()

    healthy_provider = CountingProvider(name="fake")
    async with get_sessionmaker()() as db:
        second = await run_screening_cycle(
            db, _cycle_settings(), storage, ProviderRotation([healthy_provider])
        )

    assert second.retried_errors == 1
    assert second.flagged == 1
    async with get_sessionmaker()() as db:
        refreshed = (await db.execute(select(ScreeningImage))).scalar_one()
    assert refreshed.status == "FLAGGED"


async def test_review_api_lists_scopes_and_reviews(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client, email="api-owner@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    today = dt.date.today().isoformat()

    storage = FakeStorage()
    storage.objects[f"raw/{farm_id}/{today}/wide.jpg"] = _jpeg_bytes(2000, 1000)
    storage.objects[f"raw/{farm_id}/{today}/tall.jpg"] = _jpeg_bytes(1000, 2000)

    async with get_sessionmaker()() as db:
        await run_screening_cycle(
            db, _cycle_settings(), storage, ProviderRotation([CountingProvider(name="fake")])
        )

    listed = await client.get("/api/screening/images", headers=headers)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["total"] == 2
    statuses = {row["status"] for row in body["images"]}
    assert statuses == {"FLAGGED", "HEALTHY"}
    flagged_row = next(row for row in body["images"] if row["status"] == "FLAGGED")
    assert flagged_row["pending_findings"] == 1
    assert flagged_row["latest_run"]["provider"] == "fake"

    detail = await client.get(f"/api/screening/images/{flagged_row['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    detail_body = detail.json()
    assert detail_body["findings"], "flagged image should expose its observation"
    assert detail_body["runs"][0]["verdict"] == "flagged"
    assert {run["stage"] for run in detail_body["runs"]} >= {"GATE", "SPECIALIST_SKIN"}
    # Screening is disabled in the API process's Settings → no presigned URL.
    assert detail_body["image_url"] is None

    # ---- vet review: confirm, optimistic-concurrency conflict, scoping ----
    finding = detail_body["findings"][0]
    confirm = await client.post(
        f"/api/screening/findings/{finding['id']}/review",
        json={"status": "CONFIRMED", "expected_status": "PENDING_REVIEW"},
        headers=headers,
    )
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["status"] == "CONFIRMED"
    assert confirm.json()["reviewed_at"] is not None

    # Replaying the original expected_status now conflicts.
    stale = await client.post(
        f"/api/screening/findings/{finding['id']}/review",
        json={"status": "REJECTED", "expected_status": "PENDING_REVIEW"},
        headers=headers,
    )
    assert stale.status_code == 409

    # Re-review with the CURRENT status as expected succeeds.
    overturn = await client.post(
        f"/api/screening/findings/{finding['id']}/review",
        json={"status": "REJECTED", "expected_status": "CONFIRMED", "review_note": "not visible"},
        headers=headers,
    )
    assert overturn.status_code == 200
    assert overturn.json()["status"] == "REJECTED"
    assert overturn.json()["review_note"] == "not visible"

    # Cross-farm ids must not resolve for a different farm's owner.
    other = await owner_with_farm(client, email="other-farm@farm.in", farm_name="Other Farm")
    cross = await client.get(f"/api/screening/images/{flagged_row['id']}", headers=other)
    assert cross.status_code == 404
    cross_review = await client.post(
        f"/api/screening/findings/{finding['id']}/review",
        json={"status": "CONFIRMED", "expected_status": "PENDING_REVIEW"},
        headers=other,
    )
    assert cross_review.status_code == 404

    invalid = await client.get("/api/screening/images/999999999999", headers=headers)
    assert invalid.status_code == 404


async def test_review_api_requires_authentication(client: httpx.AsyncClient) -> None:
    anonymous = await client.get("/api/screening/images")
    assert anonymous.status_code == 401


async def test_multi_goat_photo_screens_each_crop(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client, email="crops-owner@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    today = dt.date.today().isoformat()

    storage = FakeStorage()
    storage.objects[f"raw/{farm_id}/{today}/pen_group.jpg"] = _jpeg_bytes(2000, 1000)

    # Box 0 cuts a landscape region (gate flags), box 1 a portrait one
    # (gate clears) — the two goats get independent verdicts.
    provider = CountingProvider(
        name="fake", detect_boxes=[[100, 100, 600, 300], [700, 100, 250, 600]]
    )
    rotation = ProviderRotation([provider])
    async with get_sessionmaker()() as db:
        summary = await run_screening_cycle(
            db, _cycle_settings(crop_detection=True), storage, rotation
        )

    assert summary.flagged == 1  # the photo aggregates its crops
    async with get_sessionmaker()() as db:
        image = (await db.execute(select(ScreeningImage))).scalar_one()
        crops = list(
            (await db.execute(select(ScreeningCrop).order_by(ScreeningCrop.crop_index))).scalars()
        )
        runs = list((await db.execute(select(ScreeningRun))).scalars())
        findings = list((await db.execute(select(ScreeningFinding))).scalars())

    assert image.status == "FLAGGED"
    assert [crop.status for crop in crops] == ["FLAGGED", "HEALTHY"]
    assert crops[0].normalized_key and crops[0].normalized_key.endswith("-c0.jpg")
    assert crops[0].normalized_key in storage.uploaded
    stages = [run.stage for run in runs]
    assert stages.count("DETECT") == 1
    assert stages.count("GATE") == 2  # one per goat
    assert "SPECIALIST_SKIN" in stages  # only the flagged goat earned it
    assert len(findings) == 1
    assert findings[0].crop_id == crops[0].id
    assert findings[0].label == "ORF"
    # Detect + two gates + one specialist = four model calls, one photo.
    assert provider.calls == 4


async def test_detection_with_no_goats_falls_back_to_whole_photo(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, email="no-goats-owner@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    today = dt.date.today().isoformat()

    storage = FakeStorage()
    storage.objects[f"raw/{farm_id}/{today}/landscape.jpg"] = _jpeg_bytes(2000, 1000)

    provider = CountingProvider(name="fake", detect_boxes=[])
    async with get_sessionmaker()() as db:
        summary = await run_screening_cycle(
            db, _cycle_settings(crop_detection=True), storage, ProviderRotation([provider])
        )
        crops = list((await db.execute(select(ScreeningCrop))).scalars())
        runs = list((await db.execute(select(ScreeningRun))).scalars())

    # Detection found nothing → the whole photo screens as one unit.
    assert summary.flagged == 1
    assert crops == []
    # The earliest run is the detection attempt (created before the gate).
    assert min(runs, key=lambda run: run.created_at).stage == "DETECT"
    assert any(run.stage == "GATE" and run.crop_id is None for run in runs)


async def test_stats_endpoint_scores_providers(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client, email="stats-owner@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    today = dt.date.today().isoformat()

    storage = FakeStorage()
    storage.objects[f"raw/{farm_id}/{today}/wide.jpg"] = _jpeg_bytes(2000, 1000)
    storage.objects[f"raw/{farm_id}/{today}/tall.jpg"] = _jpeg_bytes(1000, 2000)

    providers = [CountingProvider(name="alpha"), CountingProvider(name="beta")]
    rotation = ProviderRotation(providers)
    async with get_sessionmaker()() as db:
        await run_screening_cycle(db, _cycle_settings(crop_detection=False), storage, rotation)

    response = await client.get("/api/screening/stats", headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    by_name = {row["provider"]: row for row in payload["providers"]}
    primary = rotation.primary_for(dt.date.today()).name
    secondary = rotation.secondary_for(dt.date.today()).name
    assert secondary is not None
    # The primary ran both gates; the secondary cross-checked the flag.
    assert by_name[primary]["gate_runs"] == 2
    assert by_name[primary]["gate_flagged"] == 1
    assert by_name[secondary]["cross_checks"] == 1
    assert by_name[secondary]["cross_check_agreements"] == 1
    assert by_name[primary]["findings_pending"] == 1


async def test_export_endpoint_returns_training_corpus(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client, email="export-owner@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    today = dt.date.today().isoformat()

    storage = FakeStorage()
    storage.objects[f"raw/{farm_id}/{today}/wide.jpg"] = _jpeg_bytes(2000, 1000)

    async with get_sessionmaker()() as db:
        await run_screening_cycle(
            db,
            _cycle_settings(crop_detection=False),
            storage,
            ProviderRotation([CountingProvider(name="fake")]),
        )

    # Nothing reviewed yet: the confirmed-only export is empty, ALL sees
    # the pending finding.
    confirmed_only = await client.get(
        "/api/screening/export", params={"vet_status": "CONFIRMED"}, headers=headers
    )
    assert confirmed_only.status_code == 200, confirmed_only.text
    assert confirmed_only.json()["record_count"] == 0
    everything = await client.get(
        "/api/screening/export", params={"vet_status": "ALL"}, headers=headers
    )
    assert everything.status_code == 200
    records = everything.json()["records"]
    assert len(records) == 1
    record = records[0]
    assert record["label"] == "ORF"
    assert record["vet_status"] == "PENDING_REVIEW"
    assert record["image_s3_key"].endswith("wide.jpg")
    assert record["detected_by"] == "fake/fake-gate-1"
    assert record["crop_box_1000"] is None

    # Confirm the finding, then the default export carries it with the vet
    # verdict attached.
    confirm = await client.post(
        f"/api/screening/findings/{record['finding_id']}/review",
        json={"status": "CONFIRMED", "expected_status": "PENDING_REVIEW"},
        headers=headers,
    )
    assert confirm.status_code == 200
    default_export = await client.get("/api/screening/export", headers=headers)
    assert default_export.status_code == 200
    assert default_export.json()["record_count"] == 1
    assert default_export.json()["records"][0]["vet_status"] == "CONFIRMED"
    assert default_export.json()["records"][0]["reviewed_at"] is not None


async def test_disease_check_walkthrough_end_to_end(client: httpx.AsyncClient) -> None:
    """The full flow: batch → presigned upload → phone PUT → worker screens
    the pre-created PENDING row with its bucket and batch recorded."""
    import app.api.screening as screening_api

    headers = await owner_with_farm(client, email="walkthrough-owner@farm.in")
    farm_id = int(headers["X-Farm-Id"])

    # The API process has screening disabled by default; point its settings
    # getter at a fully configured instance (S3 creds are the presign unit's
    # dummies — signing is offline).
    original_get_settings = screening_api.get_settings
    enabled_settings = _cycle_settings(crop_detection=False)
    screening_api.get_settings = lambda: enabled_settings  # type: ignore[assignment]
    try:
        batch = await client.post("/api/screening/batches", headers=headers)
        assert batch.status_code == 201, batch.text
        batch_id = batch.json()["id"]

        upload = await client.post(
            "/api/screening/uploads",
            json={
                "batch_id": batch_id,
                "bucket": "BREEDING",
                "file_name": "pen photo 1.jpg",
                "content_type": "image/jpeg",
            },
            headers=headers,
        )
        assert upload.status_code == 201, upload.text
        payload = upload.json()
        key = payload["s3_key"]
        assert key.startswith(f"raw/{farm_id}/{dt.date.today().isoformat()}/BREEDING/")
        assert payload["upload_url"].startswith("https://")

        # The phone's PUT lands the bytes (here: straight into fake storage).
        storage = FakeStorage()
        storage.objects[key] = _jpeg_bytes(2000, 1000)

        async with get_sessionmaker()() as db:
            summary = await run_screening_cycle(
                db,
                enabled_settings,
                storage,
                ProviderRotation([CountingProvider(name="fake")]),
            )
        assert (summary.claimed, summary.flagged) == (1, 1)

        async with get_sessionmaker()() as db:
            image = (await db.execute(select(ScreeningImage))).scalar_one()
        assert image.status == "FLAGGED"
        assert image.bucket == "BREEDING"
        assert image.batch_id == batch_id

        # Submit the walkthrough after screening already picked it up.
        submit = await client.post(f"/api/screening/batches/{batch_id}/submit", headers=headers)
        assert submit.status_code == 200, submit.text
        assert submit.json()["submitted_at"] is not None
        assert submit.json()["images_uploaded"] == 1
        assert submit.json()["images_flagged"] == 1
        assert submit.json()["buckets"][0]["bucket"] == "BREEDING"
    finally:
        screening_api.get_settings = original_get_settings  # type: ignore[assignment]


async def test_upload_without_bytes_stays_pending(client: httpx.AsyncClient) -> None:
    """A minted URL the phone never uses: the row waits as PENDING, never
    an error, and the cycle moves on."""
    import app.api.screening as screening_api

    headers = await owner_with_farm(client, email="pending-upload@farm.in")

    original_get_settings = screening_api.get_settings
    enabled_settings = _cycle_settings(crop_detection=False)
    screening_api.get_settings = lambda: enabled_settings  # type: ignore[assignment]
    try:
        batch = await client.post("/api/screening/batches", headers=headers)
        batch_id = batch.json()["id"]
        upload = await client.post(
            "/api/screening/uploads",
            json={
                "batch_id": batch_id,
                "bucket": "MALE_KIDS",
                "file_name": "kids.jpg",
                "content_type": "image/jpeg",
            },
            headers=headers,
        )
        assert upload.status_code == 201
        key = upload.json()["s3_key"]

        storage = FakeStorage()  # object never PUT
        async with get_sessionmaker()() as db:
            summary = await run_screening_cycle(
                db,
                enabled_settings,
                storage,
                ProviderRotation([CountingProvider(name="fake")]),
            )
            image = (await db.execute(select(ScreeningImage))).scalar_one()
        assert summary.claimed == 1
        assert summary.errors == 0
        assert image.status == "PENDING"
        assert any("not uploaded yet" in note for note in summary.notes)
        assert key not in storage.uploaded
    finally:
        screening_api.get_settings = original_get_settings  # type: ignore[assignment]


async def test_batch_rules_and_scoping(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client, email="batch-rules@farm.in")

    # Submitting an empty batch is refused.
    batch = await client.post("/api/screening/batches", headers=headers)
    batch_id = batch.json()["id"]
    empty_submit = await client.post(f"/api/screening/batches/{batch_id}/submit", headers=headers)
    assert empty_submit.status_code == 409

    # A second submit after a good one conflicts; cross-farm ids 404.
    other = await owner_with_farm(client, email="batch-other@farm.in", farm_name="Other")
    cross = await client.post(f"/api/screening/batches/{batch_id}/submit", headers=other)
    assert cross.status_code == 404
    unknown = await client.post("/api/screening/batches/999999999/submit", headers=headers)
    assert unknown.status_code == 404

    # Uploads require screening storage configured (503 when disabled).
    upload = await client.post(
        "/api/screening/uploads",
        json={
            "batch_id": batch_id,
            "bucket": "BREEDING",
            "file_name": "x.jpg",
            "content_type": "image/jpeg",
        },
        headers=headers,
    )
    assert upload.status_code == 503


async def test_direct_bucket_key_carries_bucket(client: httpx.AsyncClient) -> None:
    """Direct S3 uploads (no presigned flow) with a bucket segment in the
    key get the bucket recorded too."""
    headers = await owner_with_farm(client, email="direct-bucket@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    today = dt.date.today().isoformat()

    storage = FakeStorage()
    storage.objects[f"raw/{farm_id}/{today}/QUARANTINE/arrivals.jpg"] = _jpeg_bytes(1000, 2000)
    async with get_sessionmaker()() as db:
        await run_screening_cycle(
            db, _cycle_settings(), storage, ProviderRotation([CountingProvider(name="fake")])
        )
        image = (await db.execute(select(ScreeningImage))).scalar_one()
    assert image.bucket == "QUARANTINE"
    assert image.status == "HEALTHY"
