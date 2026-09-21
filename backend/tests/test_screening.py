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
import logging
import struct
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from PIL import Image
from sqlalchemy import select, update

from app.core.config import ScreeningRotationProvider, ScreeningWorkerSettings, Settings
from app.db import get_sessionmaker
from app.models import (
    Farm,
    ScreeningBatch,
    ScreeningContentClaim,
    ScreeningCrop,
    ScreeningFinding,
    ScreeningImage,
    ScreeningRun,
)
from app.schemas.screening import ScreeningFindingReviewIn
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
from app.services.screening.pipeline import (
    ERROR_RETRY_AFTER,
    MAX_DOWNLOAD_BYTES,
    MAX_SCREENING_ATTEMPTS,
    cropped_derivative_key,
    normalized_derivative_key,
    parse_raw_key,
    run_screening_cycle,
)
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
from app.services.screening.s3 import (
    _S3_CONNECT_TIMEOUT_SECONDS,
    _S3_READ_TIMEOUT_SECONDS,
    _S3_TOTAL_MAX_ATTEMPTS,
    POST_MULTIPART_OVERHEAD_BYTES,
    ScreeningObjectChangedError,
    ScreeningObjectInfo,
    ScreeningObjectMissingError,
    ScreeningObjectTooLargeError,
    ScreeningStorage,
)
from app.services.screening.specialists import (
    SpecialistKind,
    SpecialistParseError,
    parse_specialist_response,
    specialist_for_region,
)
from app.utils import today, utcnow

from .conftest import owner_with_farm, provisioned_worker_login

# Owner-provisioned worker accounts (see _role_worker_headers) share this
# first password across the suite; the login helper rotates it immediately.
WORKER_PW = "workerpass123"

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


# 2026-09-20 audit P1-2: providers answer malformed-but-valid-JSON shapes
# ("nothing found" spelled as {"conditions": null}, the key omitted, the list
# wrapped in an object, or a bare scalar/array answer).  Every one of those
# used to surface as a raw TypeError/KeyError inside the parser — which
# detonates the per-image handler — instead of this contract's own error.


@pytest.mark.parametrize(
    "answer",
    [
        json.dumps({"unrelated": True}),  # key missing entirely
        json.dumps({"conditions": None}),  # null = "nothing visible" phrasing
        json.dumps({"conditions": {}}),  # empty wrapper object
        json.dumps({"conditions": [7, "sick", None]}),  # garbage items drop, not crash
    ],
)
def test_specialist_parse_tolerates_degenerate_but_valid_json(answer: str) -> None:
    assert parse_specialist_response(answer, SpecialistKind.EYE).conditions == []


@pytest.mark.parametrize(
    "answer",
    [
        json.dumps({"conditions": 3}),  # scalar number value
        json.dumps({"conditions": "none visible"}),  # scalar string value
        json.dumps({"conditions": {"disease": "ORF"}}),  # non-empty wrapper object
        "42",  # top-level number
        '"nothing abnormal"',  # top-level string
    ],
)
def test_specialist_parse_rejects_non_list_shapes_with_controlled_error(answer: str) -> None:
    with pytest.raises(SpecialistParseError):
        parse_specialist_response(answer, SpecialistKind.EYE)


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


def test_normalize_requires_magic_and_declared_format_to_agree() -> None:
    jpeg = _jpeg_bytes(640, 480)
    with pytest.raises(ImageNormalizationError, match="magic bytes"):
        normalize_image(jpeg, max_edge=1568, expected_content_type="image/png")


def test_normalize_rejects_supported_by_pillow_but_disallowed_source_format() -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64)).save(buffer, format="GIF")
    with pytest.raises(ImageNormalizationError, match="only JPEG and PNG"):
        normalize_image(buffer.getvalue(), max_edge=1568)


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


def test_presign_post_binds_direct_upload_metadata_and_size() -> None:
    storage = ScreeningStorage(_cycle_settings())
    signed = storage.presign_post(
        "raw/1/2026-09-17/BREEDING/7-abc.jpg",
        content_type="image/jpeg",
        upload_token="x" * 32,
        max_bytes=25 * 1024 * 1024,
    )
    # SigV4 signing is local.  Unlike a PUT URL, a POST policy lets S3
    # enforce MIME, opaque pre-registration token, and byte range itself.
    assert signed.url.startswith("https://")
    assert signed.fields["Content-Type"] == "image/jpeg"
    assert signed.fields["x-amz-meta-screening-token"] == "x" * 32
    policy = json.loads(base64.b64decode(signed.fields["policy"]))
    # The browser's multipart framing counts toward S3's policy range.  A
    # bounded allowance means a file exactly at our advertised 25 MiB object
    # cap remains uploadable; the worker still rejects an object above it.
    assert ["content-length-range", 1, 25 * 1024 * 1024 + 64 * 1024] in policy["conditions"]
    assert {"Content-Type": "image/jpeg"} in policy["conditions"]
    assert {"x-amz-meta-screening-token": "x" * 32} in policy["conditions"]


@pytest.mark.parametrize("region", ["us-east-1", "ap-south-1"])
def test_aws_presigned_post_uses_the_documented_regional_virtual_host(region: str) -> None:
    """CSP/CORS can name one stable AWS origin, including us-east-1."""
    settings = _cycle_settings()
    settings.s3_region = region
    storage = ScreeningStorage(settings)
    signed = storage.presign_post(
        "raw/1/2026-09-17/BREEDING/7-abc.jpg",
        content_type="image/jpeg",
        upload_token="x" * 32,
        max_bytes=25 * 1024 * 1024,
    )
    assert signed.url == f"https://goat-photos.s3.{region}.amazonaws.com/"


def test_s3_client_bounds_sync_network_waits_for_worker_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``to_thread`` work must not inherit botocore's minute-long defaults."""
    captured: dict[str, object] = {}
    sentinel = object()

    def fake_client(*args: object, **kwargs: object) -> object:
        captured["args"] = args
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr("app.services.screening.s3.boto3.client", fake_client)
    storage = ScreeningStorage(_cycle_settings())
    assert storage._ensure_client() is sentinel
    config: Any = captured["config"]
    assert config.connect_timeout == _S3_CONNECT_TIMEOUT_SECONDS
    assert config.read_timeout == _S3_READ_TIMEOUT_SECONDS
    assert config.retries["total_max_attempts"] == _S3_TOTAL_MAX_ATTEMPTS
    assert config.s3["addressing_style"] == "virtual"
    assert config.s3["us_east_1_regional_endpoint"] == "regional"


def test_download_uses_conditional_snapshot_and_hard_stream_cap() -> None:
    class Body:
        def __init__(self, chunks: list[bytes]) -> None:
            self.chunks = chunks
            self.read_sizes: list[int] = []
            self.closed = False

        def read(self, amount: int) -> bytes:
            self.read_sizes.append(amount)
            return self.chunks.pop(0) if self.chunks else b""

        def close(self) -> None:
            self.closed = True

    class Client:
        def __init__(self, body: Body) -> None:
            self.body = body
            self.params: dict[str, object] | None = None

        def get_object(self, **params: object) -> dict[str, object]:
            self.params = params
            # A lying ContentLength is intentional: streaming must still
            # refuse the fourth byte under a three-byte cap.
            return {"ContentLength": 3, "Body": self.body}

    storage = ScreeningStorage(_cycle_settings())
    body = Body([b"abcd"])
    client = Client(body)
    storage._client = client  # type: ignore[assignment]  # isolated sync transport fake
    with pytest.raises(ScreeningObjectTooLargeError, match="while streaming"):
        storage.download("raw/1/p.jpg", max_bytes=3, etag='"stable-etag"')
    assert client.params is not None
    assert client.params["IfMatch"] == '"stable-etag"'
    assert body.read_sizes == [4]
    assert body.closed is True


def test_screening_settings_require_full_config_when_enabled() -> None:
    # The fail-closed gate: enabling screening without credentials must
    # refuse to construct Settings at all.
    with pytest.raises(ValueError, match="incomplete screening config"):
        Settings(environment="development", screening_enabled=True)


@pytest.mark.parametrize("settings_type", [Settings, ScreeningWorkerSettings])
@pytest.mark.parametrize(
    "invalid_field",
    ["s3_bucket", "s3_access_key_id", "s3_secret_access_key", "screening_anthropic_api_key"],
)
def test_screening_settings_reject_whitespace_only_standard_credentials(
    settings_type: type[Settings] | type[ScreeningWorkerSettings], invalid_field: str
) -> None:
    """A truthy SecretStr containing only spaces is not a usable credential.

    Exercise both the API and least-privilege worker projections: they share
    one screening configuration contract and must fail closed identically.
    """
    values: dict[str, object] = {
        "environment": "development",
        "screening_enabled": True,
        "s3_bucket": "goat-photos",
        "s3_access_key_id": "access-key",
        "s3_secret_access_key": "secret-key",
        "screening_anthropic_api_key": "provider-key",
    }
    values[invalid_field] = " \t "
    with pytest.raises(ValueError, match="incomplete screening config"):
        settings_type(**values)


def test_screening_settings_reject_plain_http_provider_url() -> None:
    with pytest.raises(ValueError, match="https"):
        Settings(
            environment="development",
            screening_anthropic_base_url="http://api.example.com",
        )


@pytest.mark.parametrize("settings_type", [Settings, ScreeningWorkerSettings])
def test_screening_settings_reject_stale_horizon_below_worst_cascade(
    settings_type: type[Settings] | type[ScreeningWorkerSettings],
) -> None:
    """2026-09-20 audit P2-10: a stale-PROCESSING horizon shorter than one
    worst-case cascade lets a second worker stale-reclaim a live row
    mid-cascade — duplicate runs, duplicate findings, double provider
    billing.  The pathological timeout/horizon pair must be rejected while
    Settings is being constructed (boot), not surface later as a runtime
    race.  Both the API and the least-privilege worker projection share the
    cross-validator and must fail closed identically."""
    values: dict[str, object] = {
        "environment": "development",
        "screening_enabled": True,
        "s3_bucket": "goat-photos",
        "s3_access_key_id": "access-key",
        "s3_secret_access_key": "secret-key",
        "screening_anthropic_api_key": "provider-key",
        "screening_provider_timeout_seconds": 600,
        "screening_stale_processing_after_seconds": 600,
    }
    # One provider at a 600s timeout needs (2·1+6)·600 = 4800s of horizon;
    # 600s is far inside the pathological band.
    with pytest.raises(ValueError, match="worst-case cascade"):
        settings_type(**values)


@pytest.mark.parametrize("settings_type", [Settings, ScreeningWorkerSettings])
def test_screening_settings_accept_stale_horizon_at_worst_cascade_boundary(
    settings_type: type[Settings] | type[ScreeningWorkerSettings],
) -> None:
    """The cross-validator's boundary is legal: with one provider at a 75s
    timeout one worst-case cascade is exactly (2·1+6)·75 = 600s, so a
    correctly sized horizon must still construct."""
    values: dict[str, object] = {
        "environment": "development",
        "screening_enabled": True,
        "s3_bucket": "goat-photos",
        "s3_access_key_id": "access-key",
        "s3_secret_access_key": "secret-key",
        "screening_anthropic_api_key": "provider-key",
        "screening_provider_timeout_seconds": 75,
        "screening_stale_processing_after_seconds": 600,
    }
    settings = settings_type(**values)
    assert settings.screening_stale_processing_after_seconds == 600


def test_derivative_keys_use_the_full_content_digest() -> None:
    """A shared 64-bit digest prefix must never select the same S3 object."""
    captured = dt.date(2026, 9, 17)
    first = "a" * 16 + "1" * 48
    second = "a" * 16 + "2" * 48
    first_image_key = normalized_derivative_key(7, captured, first)
    second_image_key = normalized_derivative_key(7, captured, second)
    first_crop_key = cropped_derivative_key(7, captured, first, 0)
    second_crop_key = cropped_derivative_key(7, captured, second, 0)

    assert first_image_key != second_image_key
    assert first_crop_key != second_crop_key
    assert first_image_key.endswith(f"/{first}.jpg")
    assert first_crop_key.endswith(f"/{first}-c0.jpg")


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


# 2026-09-20 audit P1-2: the detection contract must answer the same
# malformed-but-valid-JSON shapes as the specialist contract (see the
# specialist section) with a controlled outcome — an empty box list or a
# DetectionParseError the rotation's fallback chain can catch, never a raw
# TypeError that would crash the per-image handler.


@pytest.mark.parametrize(
    "answer",
    [
        json.dumps({"unrelated": True}),  # key missing entirely
        json.dumps({"goats": None}),  # null = "no goats" phrasing
        json.dumps({"goats": {}}),  # empty wrapper object
        json.dumps({"goats": [1, "two", None]}),  # garbage items drop, not crash
    ],
)
def test_detection_parse_tolerates_degenerate_but_valid_json(answer: str) -> None:
    assert parse_detection_response(answer, max_goats=4) == []


@pytest.mark.parametrize(
    "answer",
    [
        json.dumps({"goats": 3}),  # scalar number value
        json.dumps({"goats": "two goats"}),  # scalar string value
        json.dumps({"goats": {"box": [1, 2, 3, 4]}}),  # non-empty wrapper object
        "42",  # top-level number
        '"goats everywhere"',  # top-level string
    ],
)
def test_detection_parse_rejects_non_list_shapes_with_controlled_error(answer: str) -> None:
    with pytest.raises(DetectionParseError):
        parse_detection_response(answer, max_goats=4)


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
    # HEAD-probe overrides: lets a test report an oversized object without
    # materializing 25+ MB of fake bytes in memory.
    sizes: dict[str, int] = field(default_factory=dict)
    content_types: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, dict[str, str]] = field(default_factory=dict)
    etags: dict[str, str] = field(default_factory=dict)
    # Simulates an overwrite between the worker's HEAD and conditional GET.
    changed_before_download: set[str] = field(default_factory=set)
    download_attempts: list[str] = field(default_factory=list)

    @property
    def bucket(self) -> str:
        return "goat-photos"

    def list_object_keys(self, prefix: str, max_keys: int) -> list[str]:
        return sorted(k for k in self.objects if k.startswith(prefix + "/"))[:max_keys]

    def object_info(self, key: str) -> ScreeningObjectInfo | None:
        if key in self.sizes:
            size = self.sizes[key]
        else:
            blob = self.objects.get(key)
            if blob is None:
                return None
            size = len(blob)
        return ScreeningObjectInfo(
            size=size,
            etag=self.etags.get(key, f'"fake-{key}"'),
            version_id=None,
            content_type=self.content_types.get(key),
            metadata=self.metadata.get(key, {}),
        )

    def object_size(self, key: str) -> int | None:
        info = self.object_info(key)
        return None if info is None else info.size

    def download(
        self,
        key: str,
        *,
        max_bytes: int,
        etag: str | None = None,
        version_id: str | None = None,
    ) -> bytes:
        self.download_attempts.append(key)
        if key in self.changed_before_download:
            raise ScreeningObjectChangedError(f"object changed while being claimed: {key!r}")
        if key not in self.objects:
            # Mirrors the real client's NoSuchKey → ScreeningObjectMissingError.
            raise ScreeningObjectMissingError(f"object not in bucket yet: {key!r}")
        blob = self.objects[key]
        if len(blob) > max_bytes:
            raise ScreeningObjectTooLargeError(f"object exceeds {max_bytes} byte download cap")
        return blob

    def upload(self, key: str, data: bytes, content_type: str) -> None:
        self.uploaded[key] = data

    def presign_get(self, key: str) -> str:
        return f"https://fake-local/{self.bucket}/{key}"


def _cycle_settings(
    *,
    crop_detection: bool = False,
    max_images_per_cycle: int = 10,
) -> Settings:
    return Settings(
        environment="development",
        screening_enabled=True,
        s3_bucket="goat-photos",
        s3_access_key_id="test-access",
        s3_secret_access_key="test-secret",
        screening_provider="anthropic",
        screening_anthropic_api_key="test-key",
        screening_max_images_per_cycle=max_images_per_cycle,
        screening_crop_detection_enabled=crop_detection,
    )


async def _register_fake_objects(
    db: Any,
    farm_id: int,
    storage: FakeStorage,
    *,
    keys: list[str] | None = None,
) -> None:
    """Create the DB-side pre-registrations an API upload would create.

    The worker intentionally no longer converts arbitrary raw-prefix objects
    into tenant rows.  Pipeline tests that use an in-memory object store must
    therefore declare which objects were accepted through the trusted intake
    path before asking the worker to process them.
    """
    for key in keys if keys is not None else sorted(storage.objects):
        parsed = parse_raw_key(key, "raw")
        if parsed is None or parsed.farm_id != farm_id:
            continue
        db.add(
            ScreeningImage(
                farm_id=farm_id,
                bucket=parsed.bucket,
                s3_bucket=storage.bucket,
                s3_key=key,
                captured_date=parsed.captured_date,
                status="PENDING",
            )
        )
    await db.commit()


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
        await _register_fake_objects(db, farm_id, storage)
        summary = await run_screening_cycle(db, _cycle_settings(), storage, rotation)
        images = list(
            (await db.execute(select(ScreeningImage).order_by(ScreeningImage.s3_key))).scalars()
        )

    assert (summary.healthy, summary.flagged) == (1, 1)
    # Only trusted pre-registrations become rows.  Raw malformed/unknown
    # objects are intentionally invisible to the worker's intake path.
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
    business_day = today()
    capture_day = business_day.isoformat()

    storage = FakeStorage()
    storage.objects[f"raw/{farm_id}/{capture_day}/wide.jpg"] = _jpeg_bytes(2000, 1000)
    storage.objects[f"raw/{farm_id}/{capture_day}/tall.jpg"] = _jpeg_bytes(1000, 2000)

    providers = [CountingProvider(name="alpha"), CountingProvider(name="beta")]
    rotation = ProviderRotation(providers)
    async with get_sessionmaker()() as db:
        await _register_fake_objects(db, farm_id, storage)
        summary = await run_screening_cycle(db, _cycle_settings(), storage, rotation)

    assert (summary.healthy, summary.flagged) == (1, 1)
    primary = rotation.primary_for(business_day)
    secondary = rotation.secondary_for(business_day)
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
    business_day = today()
    capture_day = business_day.isoformat()

    storage = FakeStorage()
    storage.objects[f"raw/{farm_id}/{capture_day}/wide.jpg"] = _jpeg_bytes(2000, 1000)

    failing = CountingProvider(name="primary-down", fail=True)
    backup = CountingProvider(name="backup")
    # The failing provider must actually be the day's primary for the
    # fallback path to be exercised deterministically.
    providers = [failing, backup]
    rotation = ProviderRotation(providers)
    if rotation.primary_for(business_day) is not failing:
        rotation = ProviderRotation([backup, failing])

    async with get_sessionmaker()() as db:
        await _register_fake_objects(db, farm_id, storage)
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
        await _register_fake_objects(db, farm_id, storage)
        first = await run_screening_cycle(db, _cycle_settings(), storage, rotation)

    # Same bytes under a NEW key: claimed, recognized as duplicate, skipped
    # instead of being billed to the model again.
    storage.objects[f"raw/{farm_id}/{today}/b_copy.jpg"] = photo
    async with get_sessionmaker()() as db:
        await _register_fake_objects(
            db,
            farm_id,
            storage,
            keys=[f"raw/{farm_id}/{today}/b_copy.jpg"],
        )
        second = await run_screening_cycle(db, _cycle_settings(), storage, rotation)

    assert (first.claimed, first.flagged) == (1, 1)
    assert (second.claimed, second.skipped) == (1, 1)
    assert second.flagged == 0


async def test_concurrent_identical_uploads_bill_only_the_canonical_image(
    client: httpx.AsyncClient,
) -> None:
    """A database content claim closes the two-worker de-duplication race.

    Hold the first worker inside its one healthy gate call.  The second worker
    has independently claimed and normalized a different intake row, but must
    observe the durable content reservation and SKIP without reaching the
    provider.  Prior status-only de-duplication let both calls through here.
    """
    headers = await owner_with_farm(client, email="concurrent-dedupe-owner@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    capture_day = today().isoformat()
    photo = _jpeg_bytes(1000, 2000)  # healthy path = exactly one model call
    key_a = f"raw/{farm_id}/{capture_day}/a.jpg"
    key_b = f"raw/{farm_id}/{capture_day}/b_identical.jpg"
    storage = FakeStorage(objects={key_a: photo, key_b: photo})

    entered_provider = asyncio.Event()
    release_provider = asyncio.Event()

    @dataclass
    class BlockingHealthyProvider:
        name: str = "blocked-fake"
        model: str = "fake-gate-1"
        calls: int = 0

        async def complete(self, image_jpeg: bytes, system_prompt: str) -> ProviderAnswer:
            self.calls += 1
            if self.calls == 1:
                entered_provider.set()
                await release_provider.wait()
            return ProviderAnswer(
                text=HEALTHY_ANSWER,
                provider=self.name,
                model=self.model,
                latency_ms=1,
            )

    provider = BlockingHealthyProvider()
    rotation = ProviderRotation([provider])
    async with get_sessionmaker()() as db:
        await _register_fake_objects(db, farm_id, storage)

    async def worker() -> Any:
        async with get_sessionmaker()() as db:
            return await run_screening_cycle(
                db, _cycle_settings(max_images_per_cycle=1), storage, rotation
            )

    async def second_worker_decision_before_release() -> str:
        """Wait until B has decided while A remains in its blocked call.

        This is stronger than an arbitrary sleep: the old status-only check
        would make B HEALTHY here, whereas a delayed B that ran only after A
        was released would also (incorrectly) look like a passing duplicate.
        """
        terminal = {"HEALTHY", "FLAGGED", "SKIPPED", "ERROR"}
        while True:
            async with get_sessionmaker()() as db:
                rows = list(
                    (
                        await db.execute(
                            select(ScreeningImage).where(ScreeningImage.s3_key.in_([key_a, key_b]))
                        )
                    ).scalars()
                )
            finished = [row for row in rows if row.status in terminal]
            processing = [row for row in rows if row.status == "PROCESSING"]
            if len(finished) == 1 and len(processing) == 1:
                return finished[0].status
            await asyncio.sleep(0.01)

    first_task = asyncio.create_task(worker())
    second_task: asyncio.Task[Any] | None = None
    try:
        await asyncio.wait_for(entered_provider.wait(), timeout=5)
        second_task = asyncio.create_task(worker())
        second_status = await asyncio.wait_for(second_worker_decision_before_release(), timeout=5)
        assert second_status == "SKIPPED"
        release_provider.set()
        first, second = await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=10)
    finally:
        release_provider.set()
        tasks = [first_task]
        if second_task is not None:
            tasks.append(second_task)
        await asyncio.gather(*tasks, return_exceptions=True)

    assert provider.calls == 1
    assert first.claimed == second.claimed == 1
    assert first.healthy + second.healthy == 1
    assert first.skipped + second.skipped == 1

    async with get_sessionmaker()() as db:
        images = list(
            (
                await db.execute(
                    select(ScreeningImage).where(ScreeningImage.s3_key.in_([key_a, key_b]))
                )
            ).scalars()
        )
        claims = list((await db.execute(select(ScreeningContentClaim))).scalars())

    assert sorted(image.status for image in images) == ["HEALTHY", "SKIPPED"]
    assert len(claims) == 1
    assert claims[0].image_id == next(image.id for image in images if image.status == "HEALTHY")


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
        await _register_fake_objects(db, farm_id, storage)
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
    # object screens successfully once the provider is healthy again. The
    # backoff keys on the image's updated_at (its last transition), so that
    # is the timestamp the test must age.
    async with get_sessionmaker()() as db:
        aged_image = (
            await db.execute(select(ScreeningImage).where(ScreeningImage.id == image.id))
        ).scalar_one()
        aged_run = (
            await db.execute(select(ScreeningRun).where(ScreeningRun.id == run.id))
        ).scalar_one()
        aged_image.created_at = aged_image.created_at - dt.timedelta(hours=2)
        aged_image.updated_at = aged_image.updated_at - dt.timedelta(hours=2)
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


async def test_review_detail_never_presigns_the_mutable_raw_key(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L-3 (2026-09-20 audit): the review detail view used to presign the
    RAW upload key for rows without a worker-produced derivative. The raw key
    stays client-writable until the presigned POST policy expires, so the vet
    could approve bytes the uploader could still swap — the exact reason the
    dataset export refuses raw keys. Un-normalized rows must answer
    image_url=None instead."""
    import app.api.screening as screening_api

    headers = await owner_with_farm(client, email="raw-presign@farm.in")
    farm_id = int(headers["X-Farm-Id"])

    enabled_settings = _cycle_settings(crop_detection=False)
    monkeypatch.setattr(screening_api, "get_settings", lambda: enabled_settings)
    batch = await client.post("/api/screening/batches", headers=headers)
    assert batch.status_code == 201, batch.text
    upload = await client.post(
        "/api/screening/uploads",
        json={
            "batch_id": batch.json()["id"],
            "bucket": "BREEDING",
            "file_name": "pending.jpg",
            "content_type": "image/jpeg",
            "file_size": 1024,
        },
        headers=headers,
    )
    assert upload.status_code == 201, upload.text
    image_id = upload.json()["image_id"]
    raw_key = upload.json()["s3_key"]

    detail = await client.get(f"/api/screening/images/{image_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["s3_key"] == raw_key
    # PENDING row, no normalized derivative: no URL at all — never the raw
    # key, which remains client-writable until its upload policy expires.
    assert body["image_url"] is None


async def test_review_api_lists_scopes_and_reviews(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client, email="api-owner@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    today = dt.date.today().isoformat()

    storage = FakeStorage()
    storage.objects[f"raw/{farm_id}/{today}/wide.jpg"] = _jpeg_bytes(2000, 1000)
    storage.objects[f"raw/{farm_id}/{today}/tall.jpg"] = _jpeg_bytes(1000, 2000)

    async with get_sessionmaker()() as db:
        await _register_fake_objects(db, farm_id, storage)
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
        await _register_fake_objects(db, farm_id, storage)
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


async def test_flagged_parent_retries_its_errored_crop(client: httpx.AsyncClient) -> None:
    """A valid flag must not strand another goat whose cascade failed."""
    headers = await owner_with_farm(client, email="partial-crop-retry@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    capture_day = today().isoformat()
    key = f"raw/{farm_id}/{capture_day}/BREEDING/partial.jpg"
    storage = FakeStorage(objects={key: _jpeg_bytes(2000, 1000)})
    aged = utcnow() - ERROR_RETRY_AFTER - dt.timedelta(minutes=1)

    async with get_sessionmaker()() as db:
        image = ScreeningImage(
            farm_id=farm_id,
            bucket="BREEDING",
            s3_bucket=storage.bucket,
            s3_key=key,
            captured_date=dt.date.fromisoformat(capture_day),
            status="FLAGGED",
            created_at=aged,
            updated_at=aged,
        )
        db.add(image)
        await db.flush()
        db.add_all(
            [
                ScreeningCrop(
                    farm_id=farm_id,
                    image_id=image.id,
                    crop_index=0,
                    box_x=100,
                    box_y=100,
                    box_w=600,
                    box_h=300,
                    status="FLAGGED",
                ),
                ScreeningCrop(
                    farm_id=farm_id,
                    image_id=image.id,
                    crop_index=1,
                    box_x=700,
                    box_y=100,
                    box_w=250,
                    box_h=600,
                    status="ERROR",
                    error="provider outage",
                ),
            ]
        )
        await db.commit()

    provider = CountingProvider(name="fake")
    async with get_sessionmaker()() as db:
        summary = await run_screening_cycle(
            db,
            _cycle_settings(crop_detection=True),
            storage,
            ProviderRotation([provider]),
        )
        refreshed = (await db.execute(select(ScreeningImage))).scalar_one()
        crops = list(
            (await db.execute(select(ScreeningCrop).order_by(ScreeningCrop.crop_index))).scalars()
        )

    assert summary.claimed == 1
    # The reclaimed parent is a FLAGGED row retrying an errored crop, not an
    # aged ERROR row — the split metric keeps the two retry populations
    # visible separately in the cycle log.
    assert summary.retried_errors == 0
    assert summary.retried_flagged == 1
    assert refreshed.status == "FLAGGED"
    assert [crop.status for crop in crops] == ["FLAGGED", "HEALTHY"]
    assert provider.calls == 1  # only the previously errored crop re-ran


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
        await _register_fake_objects(db, farm_id, storage)
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
    business_day = today()
    capture_day = business_day.isoformat()

    storage = FakeStorage()
    storage.objects[f"raw/{farm_id}/{capture_day}/wide.jpg"] = _jpeg_bytes(2000, 1000)
    storage.objects[f"raw/{farm_id}/{capture_day}/tall.jpg"] = _jpeg_bytes(1000, 2000)

    providers = [CountingProvider(name="alpha"), CountingProvider(name="beta")]
    rotation = ProviderRotation(providers)
    async with get_sessionmaker()() as db:
        await _register_fake_objects(db, farm_id, storage)
        await run_screening_cycle(db, _cycle_settings(crop_detection=False), storage, rotation)

    response = await client.get("/api/screening/stats", headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    by_name = {row["provider"]: row for row in payload["providers"]}
    primary = rotation.primary_for(business_day).name
    secondary = rotation.secondary_for(business_day).name
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
        await _register_fake_objects(db, farm_id, storage)
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
    # Export the immutable normalized model input, not the browser's raw POST
    # target (which can legally be replayed while its short-lived policy is
    # valid). Its key and stored hash therefore describe the same bytes.
    assert record["image_s3_key"].startswith(f"screening/{farm_id}/")
    assert record["image_sha256"]
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


async def test_disease_check_walkthrough_end_to_end(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The full flow: batch → presigned upload → phone PUT → worker screens
    the pre-created PENDING row with its bucket and batch recorded."""
    import app.api.screening as screening_api

    headers = await owner_with_farm(client, email="walkthrough-owner@farm.in")
    farm_id = int(headers["X-Farm-Id"])

    # The API process has screening disabled by default; point its settings
    # getter at a fully configured instance (S3 creds are the presign unit's
    # dummies — signing is offline).
    enabled_settings = _cycle_settings(crop_detection=False)
    monkeypatch.setattr(screening_api, "get_settings", lambda: enabled_settings)
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
            "file_size": 1024,
        },
        headers=headers,
    )
    assert upload.status_code == 201, upload.text
    payload = upload.json()
    key = payload["s3_key"]
    # The API dates the key with the business timezone's `today()`, which on
    # a non-IST host can already be tomorrow relative to the machine clock.
    assert key.startswith(f"raw/{farm_id}/{today().isoformat()}/BREEDING/")
    assert payload["upload_url"].startswith("https://")

    assert payload["upload_method"] == "POST"
    assert payload["upload_fields"]["Content-Type"] == "image/jpeg"
    # The advertised client bound is the exact object cap; the worker's own
    # ceiling deliberately adds the POST policy's multipart-envelope
    # allowance so an object the policy accepted is never rejected later.
    assert payload["max_upload_bytes"] == screening_api.MAX_SCREENING_UPLOAD_BYTES
    assert (
        MAX_DOWNLOAD_BYTES
        == screening_api.MAX_SCREENING_UPLOAD_BYTES + POST_MULTIPART_OVERHEAD_BYTES
    )

    # The phone's constrained POST lands the bytes (here: straight into fake
    # storage), including the policy-bound metadata the worker verifies.
    storage = FakeStorage()
    storage.objects[key] = _jpeg_bytes(2000, 1000)

    async with get_sessionmaker()() as db:
        registered = (await db.execute(select(ScreeningImage))).scalar_one()
        assert registered.upload_token is not None
        storage.content_types[key] = "image/jpeg"
        storage.metadata[key] = {"screening-token": registered.upload_token}
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


async def test_upload_without_bytes_stays_pending(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A minted URL the phone never uses: the row waits as PENDING, never
    an error, and the cycle moves on."""
    import app.api.screening as screening_api

    headers = await owner_with_farm(client, email="pending-upload@farm.in")

    enabled_settings = _cycle_settings(crop_detection=False)
    monkeypatch.setattr(screening_api, "get_settings", lambda: enabled_settings)
    batch = await client.post("/api/screening/batches", headers=headers)
    batch_id = batch.json()["id"]
    upload = await client.post(
        "/api/screening/uploads",
        json={
            "batch_id": batch_id,
            "bucket": "MALE_KIDS",
            "file_name": "kids.jpg",
            "content_type": "image/jpeg",
            "file_size": 1024,
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
    assert image.next_attempt_at is not None
    assert image.next_attempt_at > utcnow()
    assert any("not uploaded yet" in note for note in summary.notes)
    assert key not in storage.uploaded

    # A phone that has not started its POST must not consume the next worker
    # cycle too; the row waits until its persisted backoff becomes eligible.
    async with get_sessionmaker()() as db:
        immediate = await run_screening_cycle(
            db,
            enabled_settings,
            storage,
            ProviderRotation([CountingProvider(name="fake")]),
        )
    assert immediate.claimed == 0


async def test_batch_rules_and_scoping(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.api.screening as screening_api

    headers = await owner_with_farm(client, email="batch-rules@farm.in")

    # The batch write paths share request_upload's screening_enabled gate,
    # so the rule checks run against a configured deployment...
    real_get_settings = screening_api.get_settings
    enabled_settings = _cycle_settings(crop_detection=False)
    monkeypatch.setattr(screening_api, "get_settings", lambda: enabled_settings)

    # Submitting an empty batch is refused.
    batch = await client.post("/api/screening/batches", headers=headers)
    assert batch.status_code == 201, batch.text
    batch_id = batch.json()["id"]
    empty_submit = await client.post(f"/api/screening/batches/{batch_id}/submit", headers=headers)
    assert empty_submit.status_code == 409

    # A second submit after a good one conflicts; cross-farm ids 404.
    other = await owner_with_farm(client, email="batch-other@farm.in", farm_name="Other")
    cross = await client.post(f"/api/screening/batches/{batch_id}/submit", headers=other)
    assert cross.status_code == 404
    unknown = await client.post("/api/screening/batches/999999999/submit", headers=headers)
    assert unknown.status_code == 404

    # ...and with storage unconfigured every write path, not just uploads,
    # refuses with 503 instead of half-working.
    monkeypatch.setattr(screening_api, "get_settings", real_get_settings)
    for path, payload in (
        ("/api/screening/batches", None),
        (f"/api/screening/batches/{batch_id}/submit", None),
        (
            "/api/screening/uploads",
            {
                "batch_id": batch_id,
                "bucket": "BREEDING",
                "file_name": "x.jpg",
                "content_type": "image/jpeg",
                "file_size": 1024,
            },
        ),
    ):
        refused = await client.post(path, json=payload, headers=headers)
        assert refused.status_code == 503, refused.text


async def test_direct_upload_intake_limits_reclaim_stale_batches_and_preflight_size(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The direct path cannot mint unbounded rows or permanently lose capacity.

    This covers both enforcement layers: a chosen file that cannot fit the
    object cap is rejected before it reserves a row, while S3's signed policy
    remains the authoritative check on the bytes a malicious browser sends.
    """
    import app.api.screening as screening_api

    headers = await owner_with_farm(client, email="intake-limits@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    monkeypatch.setattr(screening_api, "get_settings", lambda: _cycle_settings())

    batch_ids: list[int] = []
    for _ in range(screening_api.MAX_OPEN_SCREENING_BATCHES_PER_FARM):
        response = await client.post("/api/screening/batches", headers=headers)
        assert response.status_code == 201, response.text
        batch_ids.append(response.json()["id"])
    full = await client.post("/api/screening/batches", headers=headers)
    assert full.status_code == 429

    # Empty abandoned batches must not block this farm forever.  Their old
    # ids cannot be reused to bypass the same cap after they age out.
    async with get_sessionmaker()() as db:
        stale = await db.get(ScreeningBatch, batch_ids[0])
        assert stale is not None
        stale.created_at = (
            utcnow() - screening_api.MAX_OPEN_SCREENING_BATCH_AGE - dt.timedelta(seconds=1)
        )
        await db.commit()

    reclaimed = await client.post("/api/screening/batches", headers=headers)
    assert reclaimed.status_code == 201, reclaimed.text
    reclaimed_id = reclaimed.json()["id"]
    upload_payload = {
        "batch_id": reclaimed_id,
        "bucket": "BREEDING",
        "file_name": "pen.jpg",
        "content_type": "image/jpeg",
        "file_size": 1024,
    }
    stale_upload = await client.post(
        "/api/screening/uploads",
        json={**upload_payload, "batch_id": batch_ids[0]},
        headers=headers,
    )
    assert stale_upload.status_code == 409
    too_large = await client.post(
        "/api/screening/uploads",
        json={
            **upload_payload,
            "file_size": screening_api.MAX_SCREENING_UPLOAD_BYTES + 1,
        },
        headers=headers,
    )
    assert too_large.status_code == 422
    async with get_sessionmaker()() as db:
        assert list((await db.execute(select(ScreeningImage))).scalars()) == []

    # Fill the per-walkthrough cap directly so the assertion does not spend
    # 100 S3 signatures.  The endpoint still serializes/counts the same
    # durable rows it creates normally.
    async with get_sessionmaker()() as db:
        for index in range(screening_api.MAX_SCREENING_IMAGES_PER_BATCH):
            db.add(
                ScreeningImage(
                    farm_id=farm_id,
                    batch_id=reclaimed_id,
                    bucket="BREEDING",
                    s3_bucket="goat-photos",
                    s3_key=f"raw/{farm_id}/2026-09-17/BREEDING/batch-limit-{index}.jpg",
                    captured_date=dt.date(2026, 9, 17),
                    status="PENDING",
                )
            )
        await db.commit()
    batch_limited = await client.post(
        "/api/screening/uploads", json=upload_payload, headers=headers
    )
    assert batch_limited.status_code == 429

    # The farm-wide in-flight quota counts rows across all walkthroughs,
    # including legacy/unbatched intake, so a client cannot avoid it by
    # continually starting fresh batches.
    async with get_sessionmaker()() as db:
        additional = (
            screening_api.MAX_IN_FLIGHT_SCREENING_IMAGES_PER_FARM
            - screening_api.MAX_SCREENING_IMAGES_PER_BATCH
        )
        for index in range(additional):
            db.add(
                ScreeningImage(
                    farm_id=farm_id,
                    bucket="BREEDING",
                    s3_bucket="goat-photos",
                    s3_key=f"raw/{farm_id}/2026-09-17/BREEDING/farm-limit-{index}.jpg",
                    captured_date=dt.date(2026, 9, 17),
                    status="PENDING",
                )
            )
        await db.commit()
    farm_limited = await client.post(
        "/api/screening/uploads",
        json={**upload_payload, "batch_id": batch_ids[1]},
        headers=headers,
    )
    assert farm_limited.status_code == 429


async def test_upload_and_submit_race_has_no_post_submission_registration(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Farm → batch row locks give concurrent upload/submit one serial outcome."""
    import app.api.screening as screening_api

    headers = await owner_with_farm(client, email="upload-submit-race@farm.in")
    monkeypatch.setattr(screening_api, "get_settings", lambda: _cycle_settings())
    batch = await client.post("/api/screening/batches", headers=headers)
    assert batch.status_code == 201, batch.text
    batch_id = batch.json()["id"]
    first = {
        "batch_id": batch_id,
        "bucket": "BREEDING",
        "file_name": "first.jpg",
        "content_type": "image/jpeg",
        "file_size": 1024,
    }
    initial = await client.post("/api/screening/uploads", json=first, headers=headers)
    assert initial.status_code == 201, initial.text

    concurrent_upload, submit = await asyncio.gather(
        client.post(
            "/api/screening/uploads",
            json={**first, "file_name": "second.jpg"},
            headers=headers,
        ),
        client.post(f"/api/screening/batches/{batch_id}/submit", headers=headers),
    )
    assert submit.status_code == 200, submit.text
    assert concurrent_upload.status_code in {201, 409}
    async with get_sessionmaker()() as db:
        persisted_batch = await db.get(ScreeningBatch, batch_id)
        assert persisted_batch is not None and persisted_batch.submitted_at is not None
        image_query = select(ScreeningImage).where(ScreeningImage.batch_id == batch_id)
        registered = list((await db.execute(image_query)).scalars())
    assert len(registered) == (2 if concurrent_upload.status_code == 201 else 1)


async def test_direct_upload_and_worker_use_the_farm_business_timezone(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The API key date and worker rotation date follow the farm, not the host."""
    import app.api.screening as screening_api
    import app.services.screening.pipeline as screening_pipeline

    headers = await owner_with_farm(client, email="farm-timezone@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    farm_timezone = "Pacific/Auckland"
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        farm.timezone = farm_timezone
        await db.commit()

    api_timezones: list[str] = []

    def api_today(timezone_name: str) -> dt.date:
        api_timezones.append(timezone_name)
        return dt.date(2031, 1, 2)

    monkeypatch.setattr(screening_api, "get_settings", lambda: _cycle_settings())
    monkeypatch.setattr(screening_api, "today", api_today)
    batch = await client.post("/api/screening/batches", headers=headers)
    assert batch.status_code == 201, batch.text
    uploaded = await client.post(
        "/api/screening/uploads",
        json={
            "batch_id": batch.json()["id"],
            "bucket": "BREEDING",
            "file_name": "timezone.jpg",
            "content_type": "image/jpeg",
            "file_size": 1024,
        },
        headers=headers,
    )
    assert uploaded.status_code == 201, uploaded.text
    assert api_timezones == [farm_timezone]
    key = uploaded.json()["s3_key"]
    assert f"raw/{farm_id}/2031-01-02/" in key

    # Use a separately registered legacy row so worker verification does not
    # need the direct upload's opaque form token in this in-memory storage.
    worker_key = f"raw/{farm_id}/2031-01-02/BREEDING/worker-timezone.jpg"
    storage = FakeStorage(objects={worker_key: _jpeg_bytes(1000, 2000)})
    worker_timezones: list[str] = []

    def worker_today(timezone_name: str) -> dt.date:
        worker_timezones.append(timezone_name)
        return dt.date(2031, 1, 2)

    monkeypatch.setattr(screening_pipeline, "today", worker_today)
    async with get_sessionmaker()() as db:
        direct_row = await db.get(ScreeningImage, uploaded.json()["image_id"])
        assert direct_row is not None
        # This test only needs the API row to establish its key date; its
        # fake browser never POSTed bytes, so take it out of the worker's
        # claim set before exercising the separate registered worker image.
        direct_row.status = "SKIPPED"
        direct_row.error = "test fixture did not upload bytes"
        await db.commit()
        await _register_fake_objects(db, farm_id, storage, keys=[worker_key])
        summary = await run_screening_cycle(
            db,
            _cycle_settings(),
            storage,
            ProviderRotation([CountingProvider(name="fake")]),
        )
    assert summary.claimed == 1
    assert worker_timezones == [farm_timezone]


async def test_registered_bucket_key_carries_bucket(client: httpx.AsyncClient) -> None:
    """A trusted pre-registration retains its herd bucket through screening."""
    headers = await owner_with_farm(client, email="direct-bucket@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    today = dt.date.today().isoformat()

    storage = FakeStorage()
    storage.objects[f"raw/{farm_id}/{today}/QUARANTINE/arrivals.jpg"] = _jpeg_bytes(1000, 2000)
    async with get_sessionmaker()() as db:
        await _register_fake_objects(db, farm_id, storage)
        await run_screening_cycle(
            db, _cycle_settings(), storage, ProviderRotation([CountingProvider(name="fake")])
        )
        image = (await db.execute(select(ScreeningImage))).scalar_one()
    assert image.bucket == "QUARANTINE"
    assert image.status == "HEALTHY"


async def test_worker_ignores_unregistered_raw_prefix_objects(client: httpx.AsyncClient) -> None:
    """A raw key that merely names a real farm is not trusted intake."""
    headers = await owner_with_farm(client, email="unregistered-raw@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    storage = FakeStorage()
    key = f"raw/{farm_id}/{today().isoformat()}/BREEDING/forged.jpg"
    storage.objects[key] = _jpeg_bytes(1000, 2000)
    provider = CountingProvider(name="fake")

    async with get_sessionmaker()() as db:
        summary = await run_screening_cycle(
            db,
            _cycle_settings(),
            storage,
            ProviderRotation([provider]),
        )
        images = list((await db.execute(select(ScreeningImage))).scalars())

    assert summary.claimed == 0
    assert images == []
    assert provider.calls == 0


async def test_registered_upload_rejects_mismatched_object_metadata(
    client: httpx.AsyncClient,
) -> None:
    """The key is insufficient: token and MIME must match the row's POST policy."""
    headers = await owner_with_farm(client, email="metadata-bind@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    key = f"raw/{farm_id}/{today().isoformat()}/BREEDING/bound.jpg"
    storage = FakeStorage(objects={key: _jpeg_bytes(1000, 2000)})
    # Deliberately give the object a valid image but the wrong policy facts.
    storage.content_types[key] = "image/png"
    storage.metadata[key] = {"screening-token": "wrong" * 8}
    provider = CountingProvider(name="fake")

    async with get_sessionmaker()() as db:
        db.add(
            ScreeningImage(
                farm_id=farm_id,
                bucket="BREEDING",
                s3_bucket=storage.bucket,
                s3_key=key,
                upload_content_type="image/jpeg",
                upload_token="x" * 32,
                captured_date=today(),
                status="PENDING",
            )
        )
        await db.commit()
        summary = await run_screening_cycle(
            db,
            _cycle_settings(),
            storage,
            ProviderRotation([provider]),
        )
        image = (await db.execute(select(ScreeningImage))).scalar_one()

    assert summary.skipped == 1
    assert image.status == "SKIPPED"
    assert "token" in (image.error or "")
    assert storage.download_attempts == []
    assert provider.calls == 0


async def test_non_ascii_object_token_is_a_mismatch_not_a_crash(
    client: httpx.AsyncClient,
) -> None:
    """L-2 (2026-09-20 audit): hmac.compare_digest raises TypeError for
    non-ASCII str, so an out-of-band bucket writer putting non-ASCII bytes in
    the screening-token metadata used to escape as an unexpected pipeline
    failure — burning five download/attempt cycles with hourly retries
    before a terminal ERROR. The verdict must be the ordinary first-probe
    SKIPPED mismatch, with no download and no retry churn."""
    headers = await owner_with_farm(client, email="token-unicode@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    key = f"raw/{farm_id}/{today().isoformat()}/BREEDING/unicode.jpg"
    storage = FakeStorage(objects={key: _jpeg_bytes(1000, 2000)})
    storage.content_types[key] = "image/jpeg"
    # Non-ASCII token bytes: the exact input that raised TypeError before.
    storage.metadata[key] = {"screening-token": "не-ascii-токен" * 3}
    provider = CountingProvider(name="fake")

    async with get_sessionmaker()() as db:
        db.add(
            ScreeningImage(
                farm_id=farm_id,
                bucket="BREEDING",
                s3_bucket=storage.bucket,
                s3_key=key,
                upload_content_type="image/jpeg",
                upload_token="x" * 32,
                captured_date=today(),
                status="PENDING",
            )
        )
        await db.commit()
        summary = await run_screening_cycle(
            db,
            _cycle_settings(),
            storage,
            ProviderRotation([provider]),
        )
        image = (await db.execute(select(ScreeningImage))).scalar_one()

    assert summary.skipped == 1
    assert summary.errors == 0
    assert image.status == "SKIPPED"
    assert "token" in (image.error or "")
    assert "unexpected pipeline failure" not in (image.error or "")
    assert storage.download_attempts == []
    assert provider.calls == 0


async def test_changed_object_is_requeued_without_decoding(client: httpx.AsyncClient) -> None:
    """A conditional GET mismatch never reaches Pillow or a model call.

    Two dispositions, one invariant.  A *tokened* registration (a live
    presigned form) is a phone still mid-upload or a transient snapshot
    race: the row stays PENDING and the waiting claim does not consume the
    retry budget.  A *tokenless* legacy row has no form that could still
    deliver stable bytes, so a changed object is a storage-level fact: the
    row records ERROR and keeps consuming the attempt budget until it is
    terminal — never an infinite requeue loop.
    """
    headers = await owner_with_farm(client, email="snapshot-race@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    today_key = f"raw/{farm_id}/{today().isoformat()}/BREEDING/swap.jpg"
    legacy_key = f"raw/{farm_id}/{today().isoformat()}/BREEDING/legacy.jpg"
    storage = FakeStorage(
        objects={
            today_key: _jpeg_bytes(1000, 2000),
            legacy_key: _jpeg_bytes(1200, 800),
        }
    )
    storage.changed_before_download.update({today_key, legacy_key})
    storage.metadata[today_key] = {"screening-token": "a" * 43}
    storage.content_types[today_key] = "image/jpeg"
    provider = CountingProvider(name="fake")

    async with get_sessionmaker()() as db:
        await _register_fake_objects(db, farm_id, storage, keys=[legacy_key])
        db.add(
            ScreeningImage(
                farm_id=farm_id,
                bucket="BREEDING",
                s3_bucket=storage.bucket,
                s3_key=today_key,
                captured_date=today(),
                status="PENDING",
                upload_content_type="image/jpeg",
                upload_token="a" * 43,
            )
        )
        await db.commit()
        summary = await run_screening_cycle(
            db,
            _cycle_settings(),
            storage,
            ProviderRotation([provider]),
        )
        images = {
            image.s3_key: image for image in (await db.execute(select(ScreeningImage))).scalars()
        }

    tokened = images[today_key]
    legacy = images[legacy_key]
    assert summary.errors == 1
    # Tokened: still waiting on a live form — PENDING, backoff set, and the
    # waiting claim was refunded so slow phones never exhaust the budget.
    assert tokened.status == "PENDING"
    assert tokened.next_attempt_at is not None
    assert tokened.screening_attempts == 0
    # Tokenless: storage-level ERROR, retried while budget remains.
    assert legacy.status == "ERROR"
    assert "unavailable from storage" in (legacy.error or "")
    assert legacy.screening_attempts == 1
    # Neither row reached a decoder or a model.
    assert provider.calls == 0


# --------------------------------------------------------------------------
# Regression: audit fixes (starvation, review race, bombs, NULL buckets)
# --------------------------------------------------------------------------


async def test_abandoned_pending_uploads_expire_and_free_the_budget(
    client: httpx.AsyncClient,
) -> None:
    """The starvation bug this pins: PENDING rows whose presigned URL died
    long ago used to be re-claimed every cycle, oldest first — once they
    reached the per-cycle budget, no new key was ever screened again."""
    headers = await owner_with_farm(client, email="abandoned@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    today = dt.date.today().isoformat()

    # 12 abandoned rows (budget is 10 in _cycle_settings), aged far past
    # the presign expiry + slack horizon.
    aged = utcnow() - dt.timedelta(hours=3)
    async with get_sessionmaker()() as db:
        for i in range(12):
            db.add(
                ScreeningImage(
                    farm_id=farm_id,
                    s3_bucket="goat-photos",
                    s3_key=f"raw/{farm_id}/2026-01-01/abandoned{i}.jpg",
                    captured_date=dt.date(2026, 1, 1),
                    status="PENDING",
                    upload_content_type="image/jpeg",
                    upload_token=f"{i:032x}",
                    created_at=aged,
                    updated_at=aged,
                )
            )
        await db.commit()

    storage = FakeStorage()
    storage.objects[f"raw/{farm_id}/{today}/fresh.jpg"] = _jpeg_bytes(1000, 2000)
    provider = CountingProvider(name="fake")
    async with get_sessionmaker()() as db:
        await _register_fake_objects(db, farm_id, storage)
        summary = await run_screening_cycle(
            db, _cycle_settings(), storage, ProviderRotation([provider])
        )
        images = list(
            (await db.execute(select(ScreeningImage).order_by(ScreeningImage.id))).scalars()
        )

    assert summary.expired_uploads == 12
    assert any("expired to SKIPPED" in note for note in summary.notes)
    # The fresh photo is claimed and screened in the same cycle — the
    # abandoned rows no longer monopolize the budget.
    assert summary.claimed == 1
    assert summary.healthy == 1
    assert provider.calls == 1
    by_key = {image.s3_key: image for image in images}
    assert by_key[f"raw/{farm_id}/{today}/fresh.jpg"].status == "HEALTHY"
    for i in range(12):
        abandoned = by_key[f"raw/{farm_id}/2026-01-01/abandoned{i}.jpg"]
        assert abandoned.status == "SKIPPED"
        assert "never arrived" in (abandoned.error or "")


async def test_old_tokenless_pending_upload_can_drain_after_direct_post_migration(
    client: httpx.AsyncClient,
) -> None:
    """Pre-direct-POST records have no expiring form and must still drain."""
    headers = await owner_with_farm(client, email="legacy-pending@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    capture_day = today().isoformat()
    key = f"raw/{farm_id}/{capture_day}/BREEDING/legacy.jpg"
    storage = FakeStorage(objects={key: _jpeg_bytes(1000, 2000)})
    aged = utcnow() - dt.timedelta(hours=3)

    async with get_sessionmaker()() as db:
        db.add(
            ScreeningImage(
                farm_id=farm_id,
                bucket="BREEDING",
                s3_bucket=storage.bucket,
                s3_key=key,
                captured_date=dt.date.fromisoformat(capture_day),
                status="PENDING",
                created_at=aged,
                updated_at=aged,
            )
        )
        await db.commit()
        summary = await run_screening_cycle(
            db,
            _cycle_settings(),
            storage,
            ProviderRotation([CountingProvider(name="fake")]),
        )
        image = (await db.execute(select(ScreeningImage))).scalar_one()

    assert summary.expired_uploads == 0
    assert summary.claimed == 1
    assert summary.healthy == 1
    assert image.status == "HEALTHY"


async def test_claiming_is_fair_across_farms(client: httpx.AsyncClient) -> None:
    """One noisy farm cannot consume every slot in a small worker cycle."""
    first_headers = await owner_with_farm(client, email="fair-first@farm.in", farm_name="First")
    second_headers = await owner_with_farm(client, email="fair-second@farm.in", farm_name="Second")
    first_farm = int(first_headers["X-Farm-Id"])
    second_farm = int(second_headers["X-Farm-Id"])
    capture_day = today().isoformat()
    storage = FakeStorage()
    old = utcnow() - dt.timedelta(minutes=10)
    later = utcnow() - dt.timedelta(minutes=5)
    first_keys = [
        f"raw/{first_farm}/{capture_day}/BREEDING/noisy-{index}.jpg" for index in range(3)
    ]
    second_key = f"raw/{second_farm}/{capture_day}/BREEDING/fair.jpg"
    for key in first_keys:
        storage.objects[key] = _jpeg_bytes(1000, 2000)
    storage.objects[second_key] = _jpeg_bytes(2000, 1000)

    async with get_sessionmaker()() as db:
        for key in first_keys:
            db.add(
                ScreeningImage(
                    farm_id=first_farm,
                    bucket="BREEDING",
                    s3_bucket=storage.bucket,
                    s3_key=key,
                    captured_date=dt.date.fromisoformat(capture_day),
                    status="PENDING",
                    created_at=old,
                    updated_at=old,
                )
            )
        db.add(
            ScreeningImage(
                farm_id=second_farm,
                bucket="BREEDING",
                s3_bucket=storage.bucket,
                s3_key=second_key,
                captured_date=dt.date.fromisoformat(capture_day),
                status="PENDING",
                created_at=later,
                updated_at=later,
            )
        )
        await db.commit()
        summary = await run_screening_cycle(
            db,
            _cycle_settings(max_images_per_cycle=2),
            storage,
            ProviderRotation([CountingProvider(name="fake")]),
        )
        rows = list((await db.execute(select(ScreeningImage))).scalars())

    assert summary.claimed == 2
    first_processed = [row for row in rows if row.farm_id == first_farm and row.status != "PENDING"]
    second_processed = [
        row for row in rows if row.farm_id == second_farm and row.status != "PENDING"
    ]
    assert len(first_processed) == 1
    assert len(second_processed) == 1


async def test_abandoned_upload_sweep_is_bounded_per_cycle(client: httpx.AsyncClient) -> None:
    """Maintenance work cannot drain an arbitrary backlog before new photos."""
    headers = await owner_with_farm(client, email="bounded-sweep@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    aged = utcnow() - dt.timedelta(hours=3)
    capture_day = today().isoformat()
    storage = FakeStorage()
    fresh_key = f"raw/{farm_id}/{capture_day}/BREEDING/fresh.jpg"
    storage.objects[fresh_key] = _jpeg_bytes(1000, 2000)

    async with get_sessionmaker()() as db:
        for index in range(501):
            db.add(
                ScreeningImage(
                    farm_id=farm_id,
                    s3_bucket=storage.bucket,
                    s3_key=f"raw/{farm_id}/2026-01-01/abandoned-{index}.jpg",
                    captured_date=dt.date(2026, 1, 1),
                    status="PENDING",
                    upload_content_type="image/jpeg",
                    upload_token=f"{index:032x}",
                    created_at=aged,
                    updated_at=aged,
                )
            )
        await _register_fake_objects(db, farm_id, storage)
        summary = await run_screening_cycle(
            db,
            _cycle_settings(),
            storage,
            ProviderRotation([CountingProvider(name="fake")]),
        )
        remaining = (
            (
                await db.execute(
                    select(ScreeningImage)
                    .where(ScreeningImage.status == "PENDING")
                    .order_by(ScreeningImage.id)
                )
            )
            .scalars()
            .all()
        )

    assert summary.expired_uploads == 500
    assert summary.claimed == 1
    # Exactly one old row remains for the next bounded maintenance cycle;
    # fresh intake still made progress in this one.
    assert len(remaining) == 1
    assert remaining[0].s3_key.endswith("abandoned-500.jpg")


async def test_concurrent_reviews_resolve_to_exactly_one_verdict(
    client: httpx.AsyncClient,
) -> None:
    """Two vets reviewing the same finding at the same moment: the guarded
    UPDATE must let exactly one through — the loser gets a 409, never a
    silent last-write-wins overwrite of the training corpus."""
    headers = await owner_with_farm(client, email="race-owner@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    today = dt.date.today().isoformat()

    storage = FakeStorage()
    storage.objects[f"raw/{farm_id}/{today}/wide.jpg"] = _jpeg_bytes(2000, 1000)
    async with get_sessionmaker()() as db:
        await _register_fake_objects(db, farm_id, storage)
        await run_screening_cycle(
            db, _cycle_settings(), storage, ProviderRotation([CountingProvider(name="fake")])
        )
        finding = (await db.execute(select(ScreeningFinding))).scalar_one()

    confirm, reject = await asyncio.gather(
        client.post(
            f"/api/screening/findings/{finding.id}/review",
            json={"status": "CONFIRMED", "expected_status": "PENDING_REVIEW"},
            headers=headers,
        ),
        client.post(
            f"/api/screening/findings/{finding.id}/review",
            json={"status": "REJECTED", "expected_status": "PENDING_REVIEW"},
            headers=headers,
        ),
    )
    assert sorted([confirm.status_code, reject.status_code]) == [200, 409]
    async with get_sessionmaker()() as db:
        settled = (
            await db.execute(select(ScreeningFinding).where(ScreeningFinding.id == finding.id))
        ).scalar_one()
    expected = "CONFIRMED" if confirm.status_code == 200 else "REJECTED"
    assert settled.status == expected
    assert settled.reviewed_at is not None


def test_normalize_rejects_decompression_bomb() -> None:
    # A PNG whose IHDR claims 60k×60k (3.6 Gpx) trips Pillow's
    # decompression-bomb guard at open() — the guard's error is NOT an
    # OSError, so it must be translated, not escape as a generic failure.
    # The IHDR CRC is recomputed so the file survives the CRC check and
    # actually reaches the pixel-budget check.
    import zlib

    buffer = io.BytesIO()
    Image.new("RGB", (64, 64)).save(buffer, format="PNG")
    forged = bytearray(buffer.getvalue())
    struct.pack_into(">II", forged, 16, 60_000, 60_000)
    forged[29:33] = struct.pack(">I", zlib.crc32(bytes(forged[12:29])) & 0xFFFFFFFF)
    with pytest.raises(ImageNormalizationError, match="pixel"):
        normalize_image(bytes(forged), max_edge=1568)


async def test_batch_progress_tolerates_null_bucket_rows(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Batch-linked rows always carry a bucket in practice, but a NULL
    bucket (hand-written data, a future writer bug) must not turn the
    batches listing into a 500 — it counts toward the totals with no
    per-pen entry instead."""
    import app.api.screening as screening_api

    headers = await owner_with_farm(client, email="null-bucket@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    monkeypatch.setattr(
        screening_api, "get_settings", lambda: _cycle_settings(crop_detection=False)
    )
    batch = await client.post("/api/screening/batches", headers=headers)
    assert batch.status_code == 201, batch.text
    batch_id = batch.json()["id"]

    async with get_sessionmaker()() as db:
        db.add(
            ScreeningImage(
                farm_id=farm_id,
                batch_id=batch_id,
                s3_bucket="goat-photos",
                s3_key=f"raw/{farm_id}/2026-09-17/legacy.jpg",
                status="FLAGGED",
            )
        )
        await db.commit()

    listed = await client.get("/api/screening/batches", headers=headers)
    assert listed.status_code == 200, listed.text
    row = next(b for b in listed.json()["batches"] if b["id"] == batch_id)
    assert row["images_uploaded"] == 1
    assert row["images_screened"] == 1
    assert row["images_flagged"] == 1
    assert row["buckets"] == []


# --------------------------------------------------------------------------
# Regression: this audit's fixes (write-permission gating, ERROR backoff,
# download size cap)
# --------------------------------------------------------------------------


async def _role_worker_headers(
    client: httpx.AsyncClient, owner: dict, code: str, email: str
) -> dict:
    """Owner adds a worker wearing preset role ``code``; farm-scoped headers.

    Same shape as test_health_extended.worker_with_role: the seeded preset
    is the realistic permission bundle (VIEWER = read-only Auditor, VET =
    health.manage) rather than a hand-built role row.
    """
    team = await client.get("/api/team", headers=owner)
    assert team.status_code == 200, team.text
    role_id = next(r["id"] for r in team.json()["roles"] if r["code"] == code)
    created = await client.post(
        "/api/team/workers",
        json={"name": "Worker", "email": email, "password": WORKER_PW, "role_id": role_id},
        headers=owner,
    )
    assert created.status_code == 201, created.text
    headers, _user_id = await provisioned_worker_login(client, email, WORKER_PW)
    return headers | {"X-Farm-Id": owner["X-Farm-Id"]}


async def test_write_endpoints_demand_health_manage_not_view(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The audit's RBAC hole: create_batch / submit_batch / request_upload
    sat behind health.view — the seeded read-only Auditor preset — so an
    investor/lender account could mint presigned uploads. All three must
    demand health.manage (the VET preset), while the queue stays viewable."""
    import app.api.screening as screening_api

    headers = await owner_with_farm(client, email="rbac-owner@farm.in")
    # Enabled storage so the manage-role calls prove they got PAST the
    # permission layer and the 503 gate into real handler outcomes.
    monkeypatch.setattr(
        screening_api, "get_settings", lambda: _cycle_settings(crop_detection=False)
    )

    auditor = await _role_worker_headers(client, headers, "VIEWER", "auditor@farm.in")
    vet = await _role_worker_headers(client, headers, "VET", "walkthrough-vet@farm.in")

    # Reading the queue stays health.view...
    listed = await client.get("/api/screening/images", headers=auditor)
    assert listed.status_code == 200, listed.text

    # ...but every write refuses the read-only preset with 403.
    assert (await client.post("/api/screening/batches", headers=auditor)).status_code == 403
    upload_payload = {
        "bucket": "BREEDING",
        "file_name": "pen.jpg",
        "content_type": "image/jpeg",
        "file_size": 1024,
    }
    # A batch id the auditor cannot create; submit must still 403 on the
    # permission layer before any 404/409 scoping could apply.
    assert (
        await client.post("/api/screening/batches/1/submit", headers=auditor)
    ).status_code == 403
    assert (
        await client.post("/api/screening/uploads", json=upload_payload, headers=auditor)
    ).status_code == 403

    # The health.manage role gets through: 201 on batch and upload, and a
    # 409 on submitting a photo-less batch — domain rules, not permissions.
    batch = await client.post("/api/screening/batches", headers=vet)
    assert batch.status_code == 201, batch.text
    batch_id = batch.json()["id"]
    submit = await client.post(f"/api/screening/batches/{batch_id}/submit", headers=vet)
    assert submit.status_code == 409
    upload = await client.post(
        "/api/screening/uploads", json={**upload_payload, "batch_id": batch_id}, headers=vet
    )
    assert upload.status_code == 201, upload.text


async def test_error_rows_without_runs_back_off_before_retry(
    client: httpx.AsyncClient,
) -> None:
    """The starvation bug this pins: the ERROR retry horizon keyed on the
    latest GATE run's timestamp, so a row that errored BEFORE any run was
    written (last_at NULL) matched the predicate on every cycle and kept
    eating the claim budget. The backoff must ride the image's own
    updated_at, refreshed on every transition."""
    headers = await owner_with_farm(client, email="error-backoff@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    today = dt.date.today().isoformat()
    broken_key = f"raw/{farm_id}/2026-01-01/broken.jpg"

    async with get_sessionmaker()() as db:
        db.add(
            ScreeningImage(
                farm_id=farm_id,
                s3_bucket="goat-photos",
                s3_key=broken_key,
                captured_date=dt.date(2026, 1, 1),
                status="ERROR",
                error="download failed before any run was recorded",
            )
        )
        await db.commit()

    storage = FakeStorage()
    storage.objects[f"raw/{farm_id}/{today}/fresh.jpg"] = _jpeg_bytes(1000, 2000)
    async with get_sessionmaker()() as db:
        await _register_fake_objects(db, farm_id, storage)
        first = await run_screening_cycle(
            db, _cycle_settings(), storage, ProviderRotation([CountingProvider(name="fake")])
        )
        broken = (
            await db.execute(select(ScreeningImage).where(ScreeningImage.s3_key == broken_key))
        ).scalar_one()

    # Fresh ERROR (updated_at = now): NOT claimable within ERROR_RETRY_AFTER.
    assert utcnow() - broken.updated_at < ERROR_RETRY_AFTER
    assert first.retried_errors == 0
    assert first.claimed == 1  # only the fresh photo
    assert broken.status == "ERROR"

    # Age the row past the horizon on updated_at alone — no run rows exist
    # to age — and the bytes arrive; the retry now goes through.
    async with get_sessionmaker()() as db:
        aged = (
            await db.execute(select(ScreeningImage).where(ScreeningImage.s3_key == broken_key))
        ).scalar_one()
        aged.updated_at = aged.updated_at - dt.timedelta(hours=2)
        await db.commit()

    # Distinct bytes from fresh.jpg: the duplicate detector must not swallow
    # the recovered row as identical already-screened content.
    storage.objects[broken_key] = _jpeg_bytes(900, 2000)
    async with get_sessionmaker()() as db:
        second = await run_screening_cycle(
            db, _cycle_settings(), storage, ProviderRotation([CountingProvider(name="fake")])
        )
        recovered = (
            await db.execute(select(ScreeningImage).where(ScreeningImage.s3_key == broken_key))
        ).scalar_one()

    assert second.retried_errors == 1
    assert recovered.status == "HEALTHY"
    assert recovered.error is None


async def test_oversized_object_is_skipped_without_download(
    client: httpx.AsyncClient,
) -> None:
    """The size-cap fix: an object larger than MAX_DOWNLOAD_BYTES is
    terminally SKIPPED off the HEAD probe — no bytes transferred, no model
    call — and the claim predicate (PENDING/PROCESSING/ERROR only) can
    never pick the row up again."""
    headers = await owner_with_farm(client, email="size-cap@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    today = dt.date.today().isoformat()
    huge_key = f"raw/{farm_id}/{today}/huge.jpg"

    storage = FakeStorage()
    # Real bytes stay tiny; the HEAD probe is overridden past the cap so the
    # test need not allocate 25 MB.
    storage.objects[huge_key] = _jpeg_bytes(100, 100)
    storage.sizes[huge_key] = MAX_DOWNLOAD_BYTES + 1
    provider = CountingProvider(name="fake")

    async with get_sessionmaker()() as db:
        await _register_fake_objects(db, farm_id, storage)
        first = await run_screening_cycle(
            db, _cycle_settings(), storage, ProviderRotation([provider])
        )
        image = (await db.execute(select(ScreeningImage))).scalar_one()

    assert first.claimed == 1
    assert first.skipped == 1
    assert first.errors == 0
    assert image.status == "SKIPPED"
    assert "byte download cap" in (image.error or "")
    assert str(MAX_DOWNLOAD_BYTES + 1) in (image.error or "")
    assert storage.download_attempts == []  # refused before any transfer
    assert huge_key not in storage.uploaded
    assert provider.calls == 0  # nothing reached a model

    # SKIPPED is terminal for the claim too: a later cycle leaves it alone.
    async with get_sessionmaker()() as db:
        second = await run_screening_cycle(
            db, _cycle_settings(), storage, ProviderRotation([provider])
        )
    assert second.claimed == 0


async def _age_error_rows_for_retry() -> None:
    """Push every ERROR row past the hourly retry backoff."""
    async with get_sessionmaker()() as db:
        await db.execute(
            update(ScreeningImage)
            .where(ScreeningImage.status == "ERROR")
            .values(updated_at=utcnow() - ERROR_RETRY_AFTER - dt.timedelta(minutes=1))
        )
        await db.commit()


async def test_deterministic_failure_exhausts_its_retry_budget(
    client: httpx.AsyncClient,
) -> None:
    """A permanently failing image terminates instead of retrying forever.

    Every claim consumes one attempt; at MAX_SCREENING_ATTEMPTS the row is
    no longer eligible, and the final error says so (2026-09-18 audit M-2:
    deterministic failures previously retried hourly forever, re-downloading
    and re-billing providers on every pass).
    """
    headers = await owner_with_farm(client, email="budget-owner@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    key = f"raw/{farm_id}/{today().isoformat()}/BREEDING/poison.jpg"
    storage = FakeStorage(objects={key: _jpeg_bytes(900, 900)})
    provider = CountingProvider(name="fake", fail=True)

    async with get_sessionmaker()() as db:
        await _register_fake_objects(db, farm_id, storage)

    attempts_seen = 0
    for _ in range(MAX_SCREENING_ATTEMPTS + 2):
        async with get_sessionmaker()() as db:
            summary = await run_screening_cycle(
                db, _cycle_settings(), storage, ProviderRotation([provider])
            )
        attempts_seen += summary.claimed
        await _age_error_rows_for_retry()

    assert attempts_seen == MAX_SCREENING_ATTEMPTS
    async with get_sessionmaker()() as db:
        image = (await db.execute(select(ScreeningImage))).scalar_one()
    assert image.status == "ERROR"
    assert "terminal after" in (image.error or "")
    assert str(MAX_SCREENING_ATTEMPTS) in (image.error or "")
    assert image.screening_attempts == MAX_SCREENING_ATTEMPTS
    # Exactly one provider call per consumed attempt — nothing beyond it.
    assert provider.calls == MAX_SCREENING_ATTEMPTS


async def test_flagged_row_with_deleted_object_is_not_demoted_to_pending(
    client: httpx.AsyncClient,
) -> None:
    """A reclaimed FLAGGED photo whose object vanished keeps telling the truth.

    Old behavior demoted it to PENDING (hiding the visible flag) and later
    swept it with a false "presigned upload never arrived" even though bytes
    had landed and a goat was flagged (2026-09-18 audit M-1). The row now
    records a storage-level ERROR with its FLAGGED crops intact.
    """
    headers = await owner_with_farm(client, email="vanished-flag@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    capture_day = today().isoformat()
    key = f"raw/{farm_id}/{capture_day}/BREEDING/gone.jpg"
    aged = utcnow() - ERROR_RETRY_AFTER - dt.timedelta(minutes=1)
    # No storage object at all: the bucket lifecycle rule deleted it.
    storage = FakeStorage()

    async with get_sessionmaker()() as db:
        image = ScreeningImage(
            farm_id=farm_id,
            bucket="BREEDING",
            s3_bucket=storage.bucket,
            s3_key=key,
            captured_date=dt.date.fromisoformat(capture_day),
            status="FLAGGED",
            sha256="f" * 64,
            created_at=aged,
            updated_at=aged,
        )
        db.add(image)
        await db.flush()
        db.add(
            ScreeningCrop(
                farm_id=farm_id,
                image_id=image.id,
                crop_index=0,
                box_x=100,
                box_y=100,
                box_w=600,
                box_h=600,
                status="FLAGGED",
            )
        )
        db.add(
            ScreeningCrop(
                farm_id=farm_id,
                image_id=image.id,
                crop_index=1,
                box_x=700,
                box_y=100,
                box_w=250,
                box_h=600,
                status="ERROR",
                error="provider outage",
            )
        )
        await db.commit()

    provider = CountingProvider(name="fake")
    async with get_sessionmaker()() as db:
        summary = await run_screening_cycle(
            db, _cycle_settings(crop_detection=True), storage, ProviderRotation([provider])
        )
        refreshed = (await db.execute(select(ScreeningImage))).scalar_one()
        crops = list(
            (await db.execute(select(ScreeningCrop).order_by(ScreeningCrop.crop_index))).scalars()
        )

    assert summary.retried_flagged == 1
    assert refreshed.status == "ERROR"
    assert "unavailable from storage" in (refreshed.error or "")
    # The flagged goat's crop verdict survives the storage failure.
    assert [crop.status for crop in crops] == ["FLAGGED", "ERROR"]
    assert provider.calls == 0


async def test_reupload_takes_over_the_claim_from_a_stalled_error_owner(
    client: httpx.AsyncClient,
) -> None:
    """A farmer's re-upload of stuck bytes screens instead of being rejected.

    The stalled owner holds the content claim but has produced no result;
    the new upload takes over the claim and the owner is terminally SKIPPED
    as superseded (2026-09-18 audit L-4: the re-upload used to be rejected
    with a message asserting a screening that never happened).
    """
    from app.services.screening.images import normalize_image

    headers = await owner_with_farm(client, email="takeover@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    capture_day = today().isoformat()
    blob = _jpeg_bytes(1200, 600)  # landscape → flagged gate answer is fine
    stale_key = f"raw/{farm_id}/{capture_day}/BREEDING/stuck.jpg"
    fresh_key = f"raw/{farm_id}/{capture_day}/BREEDING/retry.jpg"
    storage = FakeStorage(objects={fresh_key: blob})
    normalized = normalize_image(blob, 1_568, "image/jpeg")
    digest = normalized.sha256

    recent = utcnow() - dt.timedelta(minutes=2)
    async with get_sessionmaker()() as db:
        stuck = ScreeningImage(
            farm_id=farm_id,
            bucket="BREEDING",
            s3_bucket=storage.bucket,
            s3_key=stale_key,
            captured_date=dt.date.fromisoformat(capture_day),
            status="ERROR",
            error="provider outage",
            sha256=digest,
            created_at=utcnow() - dt.timedelta(hours=2),
            updated_at=recent,
        )
        db.add(stuck)
        await db.flush()
        db.add(ScreeningContentClaim(farm_id=farm_id, image_id=stuck.id, sha256=digest))
        db.add(
            ScreeningImage(
                farm_id=farm_id,
                bucket="BREEDING",
                s3_bucket=storage.bucket,
                s3_key=fresh_key,
                captured_date=dt.date.fromisoformat(capture_day),
                status="PENDING",
            )
        )
        await db.commit()

    provider = CountingProvider(name="fake")
    async with get_sessionmaker()() as db:
        summary = await run_screening_cycle(
            db, _cycle_settings(), storage, ProviderRotation([provider])
        )
        images = {
            image.s3_key: image for image in (await db.execute(select(ScreeningImage))).scalars()
        }
        claim = (await db.execute(select(ScreeningContentClaim))).scalar_one()

    stuck_row = images[stale_key]
    fresh_row = images[fresh_key]
    assert summary.claimed == 1  # only the fresh row was eligible
    assert fresh_row.status in ("HEALTHY", "FLAGGED")
    assert stuck_row.status == "SKIPPED"
    assert "superseded by a newer upload" in (stuck_row.error or "")
    assert claim.image_id == fresh_row.id
    # Exactly one image's cascade ran: the takeover row. (A landscape gate
    # answer continues into the specialist stage, so the exact count is the
    # cascade's, not the point here.)
    assert provider.calls >= 1


async def test_reupload_while_owner_is_processing_reports_truthful_duplicate(
    client: httpx.AsyncClient,
) -> None:
    """A mid-flight owner means the bytes ARE being screened — say that."""
    from app.services.screening.images import normalize_image

    headers = await owner_with_farm(client, email="inflight@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    capture_day = today().isoformat()
    blob = _jpeg_bytes(800, 800)
    busy_key = f"raw/{farm_id}/{capture_day}/BREEDING/busy.jpg"
    again_key = f"raw/{farm_id}/{capture_day}/BREEDING/again.jpg"
    storage = FakeStorage(objects={again_key: blob})
    digest = normalize_image(blob, 1_568, "image/jpeg").sha256

    async with get_sessionmaker()() as db:
        busy = ScreeningImage(
            farm_id=farm_id,
            bucket="BREEDING",
            s3_bucket=storage.bucket,
            s3_key=busy_key,
            captured_date=dt.date.fromisoformat(capture_day),
            status="PROCESSING",
            sha256=digest,
            created_at=utcnow() - dt.timedelta(minutes=3),
            updated_at=utcnow(),
        )
        db.add(busy)
        await db.flush()
        db.add(ScreeningContentClaim(farm_id=farm_id, image_id=busy.id, sha256=digest))
        db.add(
            ScreeningImage(
                farm_id=farm_id,
                bucket="BREEDING",
                s3_bucket=storage.bucket,
                s3_key=again_key,
                captured_date=dt.date.fromisoformat(capture_day),
                status="PENDING",
            )
        )
        await db.commit()

    provider = CountingProvider(name="fake")
    async with get_sessionmaker()() as db:
        await run_screening_cycle(db, _cycle_settings(), storage, ProviderRotation([provider]))
        again = (
            await db.execute(select(ScreeningImage).where(ScreeningImage.s3_key == again_key))
        ).scalar_one()

    assert again.status == "SKIPPED"
    assert "currently being screened" in (again.error or "")
    assert provider.calls == 0


async def test_stale_processing_horizon_is_configurable_and_progress_gated(
    client: httpx.AsyncClient,
) -> None:
    """A 20-minute-old PROCESSING claim: reclaimed at a 10-minute horizon,
    untouched at the 30-minute default (2026-09-18 audit M-3)."""
    headers = await owner_with_farm(client, email="stale-horizon@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    capture_day = today().isoformat()
    key = f"raw/{farm_id}/{capture_day}/BREEDING/slow.jpg"
    storage = FakeStorage(objects={key: _jpeg_bytes(700, 700)})
    twenty_minutes_ago = utcnow() - dt.timedelta(minutes=20)

    async with get_sessionmaker()() as db:
        db.add(
            ScreeningImage(
                farm_id=farm_id,
                bucket="BREEDING",
                s3_bucket=storage.bucket,
                s3_key=key,
                captured_date=dt.date.fromisoformat(capture_day),
                status="PROCESSING",
                created_at=twenty_minutes_ago,
                updated_at=twenty_minutes_ago,
            )
        )
        await db.commit()

    provider = CountingProvider(name="fake")
    async with get_sessionmaker()() as db:
        default = await run_screening_cycle(
            db,
            _cycle_settings(),
            storage,
            ProviderRotation([provider]),
        )
    assert default.claimed == 0
    assert provider.calls == 0

    async with get_sessionmaker()() as db:
        row = (await db.execute(select(ScreeningImage))).scalar_one()
        row.status = "PROCESSING"
        row.updated_at = twenty_minutes_ago
        await db.commit()
        tightened = Settings(
            environment="development",
            screening_enabled=True,
            s3_bucket="goat-photos",
            s3_access_key_id="test-access",
            s3_secret_access_key="test-secret",
            screening_provider="anthropic",
            screening_anthropic_api_key="test-key",
            screening_crop_detection_enabled=False,
            screening_stale_processing_after_seconds=600,
            # The stale horizon must cover one worst-case cascade
            # ((2·providers+6) × timeout — P2-10's cross-validator); the
            # fake provider answers instantly, so a minimal timeout keeps
            # the tightened 600s horizon legal.
            screening_provider_timeout_seconds=10,
        )
        reclaimed = await run_screening_cycle(db, tightened, storage, ProviderRotation([provider]))
    assert reclaimed.claimed == 1
    assert provider.calls == 1


# --------------------------------------------------------------------------
# Regression: 2026-09-20 audit P1/P2 fixes (cycle resilience, zombie sweep,
# provider-shape tolerance, blank notes)
# --------------------------------------------------------------------------


async def _seed_two_pending_images(
    farm_id: int, storage: FakeStorage, first_key: str, second_key: str, capture_day: str
) -> None:
    """Two same-farm PENDING rows with distinct bytes; the first is older so
    the farm-fair claim order (created_at, id) processes it first."""
    older = utcnow() - dt.timedelta(minutes=10)
    newer = utcnow() - dt.timedelta(minutes=5)
    async with get_sessionmaker()() as db:
        for key, created in ((first_key, older), (second_key, newer)):
            db.add(
                ScreeningImage(
                    farm_id=farm_id,
                    bucket="BREEDING",
                    s3_bucket=storage.bucket,
                    s3_key=key,
                    captured_date=dt.date.fromisoformat(capture_day),
                    status="PENDING",
                    created_at=created,
                    updated_at=created,
                )
            )
        await db.commit()


async def test_one_images_unexpected_failure_does_not_kill_the_cycle(
    client: httpx.AsyncClient,
) -> None:
    """2026-09-20 audit P1-1a: the per-image boundary must survive a prior
    rollback.

    The FIRST claimed image crashes mid-cascade with a non-ProviderError
    while its transaction is open (the gate run is already flushed), so the
    handler's rollback genuinely discards work and expires every claimed
    identity.  The SECOND image must still be processed in the same cycle:
    the pre-fix loop re-read attributes off those expired instances
    (MissingGreenlet — including in this very handler's log line) and
    committed on a rolled-back session (PendingRollbackError), stranding
    every remaining image of the cycle behind the aborted transaction."""

    @dataclass
    class MidCascadeExploder:
        """Gate answers normally; the specialist call detonates with a
        non-ProviderError from inside an open transaction — a crash only the
        cycle loop's generic handler can absorb.  Landscape photos flag (and
        reach the specialist), portrait ones clear at the gate."""

        name: str = "exploder"
        model: str = "fake-gate-1"
        calls: int = field(default=0)

        async def complete(self, image_jpeg: bytes, system_prompt: str) -> ProviderAnswer:
            self.calls += 1
            with Image.open(io.BytesIO(image_jpeg)) as decoded:
                landscape = decoded.width > decoded.height
            if "veterinary specialist" in system_prompt:
                if landscape:
                    raise RuntimeError("injected mid-cascade failure")
                text = json.dumps({"conditions": []})
            else:
                text = FLAGGED_ANSWER if landscape else HEALTHY_ANSWER
            return ProviderAnswer(text=text, provider=self.name, model=self.model, latency_ms=1)

    headers = await owner_with_farm(client, email="cycle-resilience@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    capture_day = today().isoformat()
    first_key = f"raw/{farm_id}/{capture_day}/BREEDING/first.jpg"  # landscape → crashes
    second_key = f"raw/{farm_id}/{capture_day}/BREEDING/second.jpg"  # portrait → healthy
    storage = FakeStorage(
        objects={first_key: _jpeg_bytes(2000, 1000), second_key: _jpeg_bytes(1000, 2000)}
    )
    await _seed_two_pending_images(farm_id, storage, first_key, second_key, capture_day)

    provider = MidCascadeExploder()
    async with get_sessionmaker()() as db:
        summary = await run_screening_cycle(
            db, _cycle_settings(), storage, ProviderRotation([provider])
        )

    assert summary.claimed == 2
    assert summary.errors == 1
    assert summary.flagged == 0  # the crashing flag was rolled back, not kept
    async with get_sessionmaker()() as db:
        rows = {row.s3_key: row for row in (await db.execute(select(ScreeningImage))).scalars()}
        runs = list((await db.execute(select(ScreeningRun))).scalars())
    first, second = rows[first_key], rows[second_key]
    assert first.status == "ERROR"
    assert "unexpected pipeline failure" in (first.error or "")
    assert "injected mid-cascade failure" in (first.error or "")
    assert "terminal after" not in (first.error or "")  # budget not spent yet
    assert second.status == "HEALTHY"
    # The rollback really discarded the crashed image's flushed work: no
    # half-cascade (gate run) survived for it, while the survivor kept its
    # durable gate verdict.
    assert [run for run in runs if run.image_id == first.id] == []
    assert [run.image_id for run in runs] == [second.id]
    assert provider.calls == 3  # two gates + the detonating specialist call


async def test_one_images_commit_failure_rolls_back_and_spares_the_cycle(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """2026-09-20 audit P1-1b: a DB-level failure at the per-image COMMIT.

    The first image processes normally but its boundary commit is poisoned
    with a real constraint violation (a run row whose ``farm_id`` is NULL),
    so the flush inside the commit raises IntegrityError.  The handler must
    roll back BEFORE logging — the old code logged first and raised
    PendingRollbackError on that very logger line — and the second image
    must still complete.  The poisoned row stays PROCESSING for the
    stale-claim reclaim; its half-written work is gone."""
    import app.services.screening.pipeline as screening_pipeline

    headers = await owner_with_farm(client, email="commit-poison@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    capture_day = today().isoformat()
    first_key = f"raw/{farm_id}/{capture_day}/BREEDING/poisoned.jpg"
    second_key = f"raw/{farm_id}/{capture_day}/BREEDING/healthy.jpg"
    storage = FakeStorage(
        objects={first_key: _jpeg_bytes(1000, 2000), second_key: _jpeg_bytes(1200, 2000)}
    )
    await _seed_two_pending_images(farm_id, storage, first_key, second_key, capture_day)

    real_process = screening_pipeline._process_image

    async def poison_first_commit(
        db: Any,
        settings: Any,
        storage_arg: Any,
        rotation: Any,
        image: ScreeningImage,
        summary: Any,
        business_today: Any,
    ) -> None:
        await real_process(db, settings, storage_arg, rotation, image, summary, business_today)
        if image.s3_key == first_key:
            # Added only after the real cascade finished, so the violation
            # lands exactly at the loop's per-image boundary commit.
            db.add(
                ScreeningRun(
                    farm_id=None,  # NOT NULL violation at flush/commit time
                    image_id=image.id,
                    stage="GATE",
                    run_status="ERROR",
                    provider="poison",
                    model="poison",
                    prompt_version="poison",
                    error="test-injected row: its NULL farm_id aborts this image's commit",
                )
            )

    monkeypatch.setattr(screening_pipeline, "_process_image", poison_first_commit)

    provider = CountingProvider(name="fake")
    with caplog.at_level(logging.ERROR, logger="app.services.screening.pipeline"):
        async with get_sessionmaker()() as db:
            summary = await run_screening_cycle(
                db, _cycle_settings(), storage, ProviderRotation([provider])
            )

    assert summary.claimed == 2
    async with get_sessionmaker()() as db:
        rows = {row.s3_key: row for row in (await db.execute(select(ScreeningImage))).scalars()}
        runs = list((await db.execute(select(ScreeningRun))).scalars())
    first, second = rows[first_key], rows[second_key]
    # The poisoned image keeps its claim (PROCESSING, attempt charged) and
    # the rollback left no half-written audit rows behind.
    assert first.status == "PROCESSING"
    assert first.screening_attempts == 1
    assert [run for run in runs if run.image_id == first.id] == []
    assert second.status == "HEALTHY"
    assert [run for run in runs if run.image_id == second.id and run.stage == "GATE"]
    # The handler logged from the pre-loop snapshot id, after the rollback.
    commit_failures = [
        record.getMessage()
        for record in caplog.records
        if "committing screening image" in record.getMessage()
    ]
    assert commit_failures and str(first.id) in commit_failures[0]
    # Both cascades ran (the first one was rolled back); the survivor's
    # durable GATE run is the proof the cycle kept going.
    assert provider.calls == 2
    # PROCESSING-but-fresh is not stale yet: an immediate follow-up cycle
    # leaves the claim to the stale reclaim instead of re-billing providers.
    async with get_sessionmaker()() as db:
        followup = await run_screening_cycle(
            db, _cycle_settings(), storage, ProviderRotation([provider])
        )
    assert followup.claimed == 0


async def test_processing_row_at_attempt_budget_is_swept_to_terminal_error(
    client: httpx.AsyncClient,
) -> None:
    """2026-09-20 audit P1-3: a worker killed mid-image leaves a PROCESSING
    row whose attempt budget is already spent.  The claim predicate refuses
    rows at the budget, so without the pre-claim sweep such a row would stay
    PROCESSING forever — invisible to the retry queue, the review UI and
    operators.  Once it is also stale, the sweep terminalizes it."""
    headers = await owner_with_farm(client, email="zombie-processing@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    capture_day = today().isoformat()
    key = f"raw/{farm_id}/{capture_day}/BREEDING/zombie.jpg"
    storage = FakeStorage(objects={key: _jpeg_bytes(1000, 2000)})
    stale = utcnow() - dt.timedelta(hours=2)  # past the 30-minute default horizon

    async with get_sessionmaker()() as db:
        db.add(
            ScreeningImage(
                farm_id=farm_id,
                bucket="BREEDING",
                s3_bucket=storage.bucket,
                s3_key=key,
                captured_date=dt.date.fromisoformat(capture_day),
                status="PROCESSING",
                screening_attempts=MAX_SCREENING_ATTEMPTS,
                created_at=stale,
                updated_at=stale,
            )
        )
        await db.commit()

    provider = CountingProvider(name="fake")
    async with get_sessionmaker()() as db:
        first = await run_screening_cycle(
            db, _cycle_settings(), storage, ProviderRotation([provider])
        )
        row = (await db.execute(select(ScreeningImage))).scalar_one()

    assert first.terminated_processing == 1
    assert any("terminated to ERROR" in note for note in first.notes)
    assert first.claimed == 0  # never re-claimed: the budget is gone
    assert provider.calls == 0
    assert row.status == "ERROR"
    assert "terminal" in (row.error or "")
    assert "re-upload" in (row.error or "")
    assert row.screening_attempts == MAX_SCREENING_ATTEMPTS

    # ERROR at the budget is invisible to the claim predicate: later cycles
    # leave the terminal row alone instead of re-billing providers.
    async with get_sessionmaker()() as db:
        second = await run_screening_cycle(
            db, _cycle_settings(), storage, ProviderRotation([provider])
        )
        refreshed = (await db.execute(select(ScreeningImage))).scalar_one()
    assert second.claimed == 0
    assert second.terminated_processing == 0
    assert refreshed.status == "ERROR"


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_finding_review_input_maps_blank_notes_to_none(blank: str | None) -> None:
    """2026-09-20 audit P2-11(a): ``ck_screening_findings_review_note_nonblank``
    rejects blank-but-non-NULL notes, so a whitespace-only review note used to
    surface as an unhandled IntegrityError (500); the request validator maps
    every blank spelling to None."""
    assert ScreeningFindingReviewIn(status="CONFIRMED", review_note=blank).review_note is None


def test_finding_review_input_preserves_a_real_note() -> None:
    reviewed = ScreeningFindingReviewIn(status="REJECTED", review_note="  not visible  ")
    assert reviewed.review_note == "not visible"


async def test_blank_specialist_note_persists_as_null_not_integrity_error(
    client: httpx.AsyncClient,
) -> None:
    """2026-09-20 audit P2-11(b): a model that answered ``note: ""`` (or a
    whitespace note that pydantic's strip reduces to "") must land as NULL.
    ``ck_screening_findings_text_nonblank`` rejects the blank string, and
    pre-fix that IntegrityError rolled the whole cascade back into the retry
    loop instead of recording the flag."""
    headers = await owner_with_farm(client, email="blank-note@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    capture_day = today().isoformat()
    key = f"raw/{farm_id}/{capture_day}/wide.jpg"
    storage = FakeStorage(objects={key: _jpeg_bytes(2000, 1000)})

    @dataclass
    class BlankNoteProvider:
        name: str = "blank-note"
        model: str = "fake-gate-1"
        calls: int = field(default=0)

        async def complete(self, image_jpeg: bytes, system_prompt: str) -> ProviderAnswer:
            self.calls += 1
            if "veterinary specialist" in system_prompt:
                if "skin, lips" in system_prompt:
                    text = json.dumps(
                        {
                            "conditions": [
                                {
                                    "disease": "ORF",
                                    "confidence": 0.72,
                                    "severity": "moderate",
                                    "note": "",
                                }
                            ]
                        }
                    )
                else:
                    text = json.dumps({"conditions": []})
            else:
                text = FLAGGED_ANSWER
            return ProviderAnswer(text=text, provider=self.name, model=self.model, latency_ms=1)

    provider = BlankNoteProvider()
    async with get_sessionmaker()() as db:
        await _register_fake_objects(db, farm_id, storage)
        summary = await run_screening_cycle(
            db, _cycle_settings(), storage, ProviderRotation([provider])
        )
        image = (await db.execute(select(ScreeningImage))).scalar_one()
        findings = list((await db.execute(select(ScreeningFinding))).scalars())

    assert (summary.claimed, summary.flagged) == (1, 1)
    assert image.status == "FLAGGED"
    assert len(findings) == 1
    assert findings[0].label == "ORF"
    assert findings[0].note is None  # blank coerced to NULL: the CHECK never fires


async def test_cycle_falls_over_when_primary_answers_garbage_json(
    client: httpx.AsyncClient,
) -> None:
    """2026-09-20 audit P1-2 (cascade level): a garbage-but-HTTP-200 provider
    answer must fail over to the rotation's next provider exactly like a
    transport outage — the parse failure is a ProviderResponseError inside
    the gate chain, never a raw TypeError detonating the per-image handler."""
    headers = await owner_with_farm(client, email="garbage-json@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    capture_day = today().isoformat()
    key = f"raw/{farm_id}/{capture_day}/tall.jpg"
    storage = FakeStorage(objects={key: _jpeg_bytes(1000, 2000)})

    @dataclass
    class GarbageJsonProvider:
        name: str = "garbage"
        model: str = "fake-garbage-1"
        calls: int = field(default=0)

        async def complete(self, image_jpeg: bytes, system_prompt: str) -> ProviderAnswer:
            self.calls += 1
            return ProviderAnswer(
                text="no json here at all",
                provider=self.name,
                model=self.model,
                latency_ms=1,
            )

    garbage = GarbageJsonProvider()
    healthy = CountingProvider(name="backup")
    # The garbage provider must actually be the day's primary for the
    # mid-cycle fallback path to be exercised deterministically.
    rotation = ProviderRotation([garbage, healthy])
    if rotation.primary_for(today()) is not garbage:
        rotation = ProviderRotation([healthy, garbage])

    async with get_sessionmaker()() as db:
        await _register_fake_objects(db, farm_id, storage)
        summary = await run_screening_cycle(db, _cycle_settings(), storage, rotation)
        runs = list((await db.execute(select(ScreeningRun))).scalars())

    assert (summary.claimed, summary.healthy) == (1, 1)
    assert all(run.run_status == "OK" for run in runs)
    gate = next(run for run in runs if run.stage == "GATE")
    assert gate.provider == "backup"
    assert gate.detail is not None
    assert gate.detail.get("fallbacks_failed") == ["garbage"]
    assert garbage.calls == 1  # it failed the contract, not the transport
    assert healthy.calls == 1  # and the backup carried the photo
