"""Detector proposals need at least40 of1000 units on both axes."""

import json

import pytest

from app.services.screening.detect import DetectionParseError, parse_detection_response


@pytest.mark.parametrize(
    ("width", "height", "expected"),
    [(39, 80, []), (40, 80, [(100, 100, 40, 80)]), (80, 39, []), (80, 40, [(100, 100, 80, 40)])],
    ids=["width-below", "width-minimum", "height-below", "height-minimum"],
)
def test_literal_minimum_box_keeps_valid_evidence_and_drops_coordinate_noise(
    width: int,
    height: int,
    expected: list[tuple[int, int, int, int]],
) -> None:
    answer = json.dumps({"goats": [{"box": [100, 100, width, height]}]})
    try:
        boxes = parse_detection_response(answer, max_goats=8)
    except DetectionParseError as exc:
        pytest.fail(f"A valid in-frame detector proposal must parse before size filtering: {exc}")
    assert [(box.x, box.y, box.w, box.h) for box in boxes] == expected
