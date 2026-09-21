"""The gate stage: one prompt, one JSON contract, every provider.

The gate is the cheap first look at every photo. Its only job is to decide
"anything abnormal?" — healthy verdicts stop the cascade; flagged photos
carry observations that Phase 2 specialists will refine. Keeping the prompt
and the response schema in this module (not in the provider adapters) is
what makes Phase 2's round-robin rotation a fair comparison: every model
answers the same question under the same rules.
"""

from __future__ import annotations

import json
import re
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

# Bump when the prompt wording changes; runs record it so per-provider
# accuracy stats never silently compare answers to different questions.
GATE_PROMPT_VERSION = "gate-2026-09.1"

MAX_OBSERVATIONS = 8

GATE_SYSTEM_PROMPT = """You are a veterinary screening assistant for a goat herd.
You see one photograph that may contain one or more goats.

Decide whether ANY goat shows a visible health abnormality.

Look specifically for:
- Skin/lesions: crusty scabs around lips or nostrils (orf), pox nodules,
  circular hairless patches (ringworm), mange scabs or hair loss, abscess
  swellings under the jaw/neck (caseous lymphadenitis)
- Eyes: cloudiness, heavy discharge, visible irritation (pinkeye); very pale
  inner eyelids (anemia)
- Hooves/legs: limping posture, swollen or infected tissue between hooves
  (foot rot), blisters around hooves or mouth
- Udder: visible swelling or redness
- General: severe undercondition, large wounds, active diarrhea stains

Rules:
- Only report what is actually VISIBLE in the image. Do not speculate about
  conditions with no external signs.
- If the photo is too dark, blurry, or contains no goat, do not flag it;
  set quality_problem true and explain in the note.
- confidence is 0.0-1.0.

Answer with ONLY a JSON object, no other text, in exactly this shape:
{
  "flagged": true|false,
  "confidence": 0.0-1.0,
  "quality_problem": true|false,
  "observations": [
    {"region": "mouth|eye|hoof|udder|skin|general",
     "label": "short abnormality label in English, max 80 chars",
     "confidence": 0.0-1.0,
     "note": "one-sentence description of what you see"}
  ]
}
"observations" must be empty when flagged is false."""


class GateObservation(BaseModel):
    """One visible abnormality the model wants a human to check."""

    model_config = ConfigDict(str_strip_whitespace=True)

    region: str = Field(min_length=1, max_length=40)
    label: str = Field(min_length=1, max_length=80)
    confidence: float = Field(ge=0.0, le=1.0)
    note: str | None = Field(default=None, max_length=500)


class GateResponse(BaseModel):
    """The gate contract every provider must satisfy."""

    flagged: bool
    confidence: float = Field(ge=0.0, le=1.0)
    quality_problem: bool = False
    observations: list[GateObservation] = Field(default_factory=list, max_length=MAX_OBSERVATIONS)

    @field_validator("observations")
    @classmethod
    def _observations_require_flag(
        cls, value: list[GateObservation], info: ValidationInfo
    ) -> list[GateObservation]:
        # A "healthy" verdict with attached observations would deadlock the
        # review queue (nothing to review, but findings exist); normalize
        # instead of rejecting so a chatty model cannot wedge the pipeline.
        if info.data.get("flagged") is False:
            return []
        return value


class GateParseError(Exception):
    """The model's answer could not be coerced into the gate contract."""


# ```json fences and stray prose around the object are common; extract the
# first balanced JSON object rather than demanding byte-perfect answers.
_JSON_OBJECT_START = re.compile(r"\{")


def _extract_json_object(text: str) -> str:
    depth = 0
    in_string = False
    escaped = False
    start = _JSON_OBJECT_START.search(text)
    if start is None:
        raise GateParseError("no JSON object found in model answer")
    for index in range(start.start(), len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start.start() : index + 1]
    raise GateParseError("unterminated JSON object in model answer")


def parse_gate_response(text: str) -> GateResponse:
    """Coerce a provider's raw answer text into a validated GateResponse."""
    try:
        return GateResponse.model_validate(json.loads(_extract_json_object(text)))
    except GateParseError:
        raise
    except ValueError as exc:
        raise GateParseError(f"model answer violated the gate contract: {exc}") from exc


def answer_list(raw: object, key: str) -> list[object]:
    """Extract a list-typed field from a parsed model answer.

    Providers answer ``{"goats": null}`` / ``{"conditions": null}`` (a very
    plausible LLM phrasing of "nothing found"): a null, missing or otherwise
    empty field parses as an empty list.  A *present but non-list* value
    (scalar/object) raises GateParseError — which the downstream parsers
    convert into *their* ParseError, the exception the rotation's fallback
    chain catches — instead of letting a raw ``TypeError``/``AttributeError``
    detonate the generic per-image handler and strand the row.
    """
    if not isinstance(raw, dict):
        raise GateParseError(f"model answer is a {type(raw).__name__}, not a JSON object")
    value = raw.get(key) or []
    if not isinstance(value, list):
        raise GateParseError(f"model answer field {key!r} is a {type(value).__name__}, not a list")
    return value


class GateInstruction(NamedTuple):
    """What every provider sends: one system prompt, one image."""

    system_prompt: str
    prompt_version: str


def gate_instruction() -> GateInstruction:
    return GateInstruction(system_prompt=GATE_SYSTEM_PROMPT, prompt_version=GATE_PROMPT_VERSION)
