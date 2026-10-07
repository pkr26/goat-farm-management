"""Real detector replies and JPEG crops preserve bounded goat evidence."""

import io
import json
from typing import Any

import httpx
import pytest
from PIL import Image
from pydantic import ValidationError
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import ScreeningCrop
from app.schemas.screening import ScreeningCropOut
from app.services.screening.detect import DetectionParseError, parse_detection_response
from app.services.screening.images import CropError, crop_image
from app.services.screening.pipeline import run_screening_cycle
from app.services.screening.rotation import ProviderRotation
from app.utils import today

from .conftest import owner_with_farm
from .test_screening import (
    CountingProvider,
    FakeStorage,
    _cycle_settings,
    _jpeg_bytes,
    _register_fake_objects,
)


def _boxes(items: list[Any], limit: int = 30) -> list[tuple[int, int, int, int]]:
    try:
        parsed = parse_detection_response(json.dumps({"goats": items}), max_goats=limit)
    except DetectionParseError as exc:
        pytest.fail(f"Supported detector replies must keep usable goat evidence: {exc}")
    except ValueError as exc:
        if "values to unpack" not in str(exc):
            raise
        pytest.fail(
            f"Malformed coordinate tuples must be dropped before the next valid goat: {exc}"
        )
    return [(box.x, box.y, box.w, box.h) for box in parsed]


def test_provider_coordinate_overflow_clamps_to_the_literal_1000_unit_frame() -> None:
    assert _boxes([{"box": [1001, 100, 80, 80]}, {"box": [100, 100, 1001, 1001]}]) == [
        (1000, 100, 80, 80),
        (100, 100, 1000, 1000),
    ]


@pytest.mark.parametrize("count", [20, 21], ids=["twentieth", "hallucinated-extra"])
def test_one_detector_reply_keeps_at_most_twenty_usable_goats(count: int) -> None:
    items = [{"box": [i, 100, 80, 80]} for i in range(count)]
    assert _boxes(items) == [(i, 100, 80, 80) for i in range(20)]


@pytest.mark.parametrize("bad", [[1, 2, 3], [1, 2, 3, 4, 5]], ids=["short", "long"])
def test_malformed_tuple_does_not_hide_the_following_real_goat(bad: list[int]) -> None:
    assert _boxes([{"box": bad}, {"box": [100, 200, 80, 90]}]) == [(100, 200, 80, 90)]


def test_crop_coordinates_use_the_literal_1000_scale_and_five_percent_frame_margin() -> None:
    try:
        artifact = crop_image(_jpeg_bytes(1000, 1000), (100, 100, 400, 400))
    except CropError as exc:
        pytest.fail(f"A supported in-frame goat box must produce the expected camera region: {exc}")
    assert (artifact.width, artifact.height) == (500, 500)
    with Image.open(io.BytesIO(artifact.data)) as decoded:
        assert decoded.size == (500, 500)


@pytest.mark.parametrize(
    ("width", "height", "box"),
    [(48, 160, (500, 100, 40, 600)), (160, 48, (100, 500, 600, 40))],
    ids=["narrow", "short"],
)
def test_supported_normalized_box_must_not_emit_a_sub_eight_pixel_crop(
    width: int, height: int, box: tuple[int, int, int, int]
) -> None:
    # Both boxes meet the detector's40-unit noise threshold. Real admitted
    # small raster dimensions can still leave fewer than8 actual pixels.
    assert _boxes([{"box": list(box)}]) == [box]
    with pytest.raises(CropError, match="degenerate region"):
        crop_image(_jpeg_bytes(width, height), box)


async def test_committed_model_generated_crop_supports_its_declared_native_wire_adapter(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    key = f"raw/{farm_id}/{today().isoformat()}/native-crop.jpg"
    storage = FakeStorage(objects={key: _jpeg_bytes(480, 960)})
    provider = CountingProvider(name="native-healthy", detect_boxes=[[100, 100, 400, 400]])
    async with get_sessionmaker()() as db:
        await _register_fake_objects(db, farm_id, storage)
        summary = await run_screening_cycle(
            db, _cycle_settings(crop_detection=True), storage, ProviderRotation([provider])
        )
        assert summary.healthy == 1
        crop = (await db.execute(select(ScreeningCrop))).scalar_one()
        try:
            native = ScreeningCropOut.model_validate(crop)
        except ValidationError as exc:
            pytest.fail(
                f"A genuinely screened committed goat crop must adapt to its wire facts: {exc}"
            )
        assert native.id == crop.id and native.crop_index == 0
        assert (native.box_x, native.box_y, native.box_w, native.box_h) == (100, 100, 400, 400)
        assert native.status == "HEALTHY" and native.error is None and native.image_url is None
        assert native.created_at == crop.created_at


def test_solid_crop_canonical_jpeg_has_a_small_content_optimized_huffman_alphabet() -> None:
    buffer = io.BytesIO()
    with Image.new("RGB", (240, 160), (29, 91, 173)) as frame:
        frame.save(buffer, format="JPEG", quality=95)
    try:
        artifact = crop_image(buffer.getvalue(), (200, 200, 400, 400))
    except CropError as exc:
        pytest.fail(f"A genuine small uniform goat-region artifact must encode successfully: {exc}")
    data = artifact.data
    cursor = 2
    symbols = 0
    tables = 0
    while cursor < len(data):
        assert data[cursor] == 0xFF
        marker = data[cursor + 1]
        if marker == 0xDA:
            break  # entropy-coded scan follows; headers are complete
        length = int.from_bytes(data[cursor + 2 : cursor + 4], "big")
        payload = data[cursor + 4 : cursor + 2 + length]
        if marker == 0xC4:
            offset = 0
            while offset < len(payload):
                count = sum(payload[offset + 1 : offset + 17])
                symbols += count
                tables += 1
                offset += 17 + count
            assert offset == len(payload)
        cursor += 2 + length
    assert tables == 4
    # A uniform region needs a small alphabet. Generic fixed JPEG tables
    # carry hundreds of symbols; canonical optimized artifacts omit them.
    assert symbols <= 16
