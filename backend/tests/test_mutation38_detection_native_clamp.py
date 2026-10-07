"""The native integer-box clamp conserves a nonempty normalized coordinate extent."""

import pytest

from app.services.screening.detect import DetectionBox


@pytest.mark.parametrize("height", [1, 0], ids=["small-positive-extent", "empty-coordinate-noise"])
def test_native_box_clamp_keeps_one_unit_and_repairs_empty_height(height: int) -> None:
    box = DetectionBox(x=100, y=200, w=120, h=height)
    clamped = box.clamped()
    assert (clamped.x, clamped.y, clamped.w, clamped.h) == (100, 200, 120, 1)
    assert box == DetectionBox(x=100, y=200, w=120, h=height)
