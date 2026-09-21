"""Detection stage: split one multi-goat photo into per-goat crops.

One VLM call per photo returns a bounding box per goat in 0-1000 normalized
coordinates (the Anthropic grounding convention; OpenAI-shaped providers
follow the same prompt). Each crop then runs the full cascade
independently, so the gate's "healthy → stop" decision happens per goat,
not per photo.

Safety net: a detection failure, or zero boxes found, falls back to
screening the whole photo — a detection miss can never leave a herd
un-screened.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from .gate import GateParseError, _extract_json_object, answer_list

DETECT_PROMPT_VERSION = "detect-26.09.1"

# Degenerate boxes (a whisker-wide sliver) are detection noise, not goats.
MIN_BOX_SIZE = 40  # of 1000; ~4% of the frame edge

MAX_DETECTED_GOATS = 20


@dataclass(frozen=True)
class DetectionBox:
    """One goat, 0-1000 normalized: top-left corner plus size."""

    x: int
    y: int
    w: int
    h: int

    def clamped(self) -> DetectionBox:
        return DetectionBox(
            x=max(0, min(self.x, 1000)),
            y=max(0, min(self.y, 1000)),
            w=max(1, min(self.w, 1000)),
            h=max(1, min(self.h, 1000)),
        )


class _DetectedGoat(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    box: list[int] = Field(min_length=4, max_length=4)


class DetectionResponse(BaseModel):
    goats: list[_DetectedGoat] = Field(default_factory=list, max_length=MAX_DETECTED_GOATS)


DETECT_SYSTEM_PROMPT = f"""You examine one photograph that may contain several goats.
Find every goat in the image (whole animals or clearly visible partial animals).

Answer with ONLY a JSON object, no other text, in exactly this shape:
{{
  "goats": [
    {{"box": [x, y, width, height]}}
  ]
}}

box coordinates are integers on a 0-1000 scale measured from the TOP-LEFT
corner of the image: x = left edge, y = top edge, width and height extend
right and down. Include a little margin around each animal (ears, tail,
legs). Cover at least {MIN_BOX_SIZE} units in both width and height.

Return an empty "goats" list only when the photo contains no goat at all."""


class DetectionParseError(Exception):
    """The detection answer could not be coerced into boxes."""


def parse_detection_response(text: str, max_goats: int) -> list[DetectionBox]:
    """Model answer → validated boxes, de-noised and capped.

    Degenerate boxes are dropped, boxes are clamped into the 0-1000 frame,
    and anything past ``max_goats`` is ignored (a photo claiming thirty
    goats is a hallucination, not a herd).
    """
    try:
        raw = json.loads(_extract_json_object(text))
        # A null/missing "goats" (e.g. {"goats": null} — a plausible "no
        # goats" phrasing) parses as "nothing found"; a present-but-non-list
        # value (scalar/object) surfaces as DetectionParseError so the
        # rotation's fallback chain tries the next provider — a raw TypeError
        # here would instead detonate the per-image handler.
        items = answer_list(raw, "goats")
    except (ValueError, GateParseError) as exc:
        raise DetectionParseError(f"no valid JSON in detection answer: {exc}") from exc
    boxes: list[DetectionBox] = []
    for item in items[:MAX_DETECTED_GOATS]:
        try:
            goat = _DetectedGoat.model_validate(item)
        except ValueError:
            continue  # a malformed box is dropped, not fatal
        x, y, w, h = goat.box
        if w < MIN_BOX_SIZE or h < MIN_BOX_SIZE:
            continue
        boxes.append(DetectionBox(x=x, y=y, w=w, h=h).clamped())
        if len(boxes) >= max_goats:
            break
    return boxes


def detection_instruction() -> tuple[str, str]:
    """(system prompt, prompt version) — tuple keeps providers decoupled
    from this module's versioning."""
    return DETECT_SYSTEM_PROMPT, DETECT_PROMPT_VERSION


@dataclass(frozen=True)
class DetectionCallResult:
    boxes: list[DetectionBox]
    provider: str
    model: str
    prompt_version: str
    latency_ms: int
