"""Specialist stage: region-focused disease refinement of gate findings.

The gate says "something near the mouth"; the specialist is the follow-up
question — "given the mouth/lips, which of THESE conditions is visible?".
One specialist per body-region kind per image, bounded vocabularies so the
labels stay reportable and the review corpus stays trainable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .gate import GateParseError, _extract_json_object, answer_list  # shared tolerance
from .providers import ProviderAnswer, ProviderResponseError, VisionProvider

MAX_SPECIALIST_CONDITIONS = 5


class SpecialistKind(Enum):
    SKIN = "SPECIALIST_SKIN"
    EYE = "SPECIALIST_EYE"
    HOOF = "SPECIALIST_HOOF"
    UDDER = "SPECIALIST_UDDER"
    GENERAL = "SPECIALIST_GENERAL"


# Bounded, reportable disease vocabularies per specialist. Unknown guesses
# are coerced to OTHER (kept, but never silently renamed); anything the
# gate called "general" lands in the whole-animal specialist.
DISEASE_VOCABULARY: Final[dict[SpecialistKind, tuple[str, ...]]] = {
    SpecialistKind.SKIN: (
        "ORF",
        "GOAT_POX",
        "RINGWORM",
        "MANGE",
        "CASEOUS_LYMPHADENITIS",
        "WOUND",
        "OTHER",
    ),
    SpecialistKind.EYE: ("PINK_EYE", "ANEMIA_FAMACHA", "EYE_TRAUMA", "OTHER"),
    SpecialistKind.HOOF: ("FOOT_ROT", "FMD_SUSPECT", "LAMENESS", "OVERGROWN_HOOVES", "OTHER"),
    SpecialistKind.UDDER: ("MASTITIS_CLINICAL", "UDDER_TRAUMA", "OTHER"),
    SpecialistKind.GENERAL: ("UNDERCONDITION", "DIARRHEA_STAINS", "LETHARGY_POSTURE", "OTHER"),
}

SPECIALIST_PROMPT_VERSIONS: Final[dict[SpecialistKind, str]] = {
    SpecialistKind.SKIN: "spec-skin-26.09.1",
    SpecialistKind.EYE: "spec-eye-26.09.1",
    SpecialistKind.HOOF: "spec-hoof-26.09.1",
    SpecialistKind.UDDER: "spec-udder-26.09.1",
    SpecialistKind.GENERAL: "spec-general-26.09.1",
}

_REGION_TO_KIND: Final[dict[str, SpecialistKind]] = {
    "mouth": SpecialistKind.SKIN,
    "nose": SpecialistKind.SKIN,
    "face": SpecialistKind.SKIN,
    "skin": SpecialistKind.SKIN,
    "eye": SpecialistKind.EYE,
    "hoof": SpecialistKind.HOOF,
    "hooves": SpecialistKind.HOOF,
    "leg": SpecialistKind.HOOF,
    "legs": SpecialistKind.HOOF,
    "udder": SpecialistKind.UDDER,
    "general": SpecialistKind.GENERAL,
    "body": SpecialistKind.GENERAL,
}


def specialist_for_region(region: str | None) -> SpecialistKind:
    """Gate region string → specialist kind; unknown regions get the
    whole-animal specialist rather than being dropped."""
    if region is None:
        return SpecialistKind.GENERAL
    return _REGION_TO_KIND.get(region.strip().lower(), SpecialistKind.GENERAL)


class SpecialistCondition(BaseModel):
    """One refined finding: a vocabulary disease + confidence + severity."""

    model_config = ConfigDict(str_strip_whitespace=True)

    disease: str = Field(min_length=2, max_length=80)
    confidence: float = Field(ge=0.0, le=1.0)
    severity: str = Field(default="moderate", pattern="^(mild|moderate|severe)$")
    note: str | None = Field(default=None, max_length=500)

    @field_validator("disease")
    @classmethod
    def _uppercase_disease(cls, value: str) -> str:
        return value.upper()


class SpecialistResponse(BaseModel):
    conditions: list[SpecialistCondition] = Field(
        default_factory=list, max_length=MAX_SPECIALIST_CONDITIONS
    )


class SpecialistParseError(Exception):
    """The specialist answer could not be coerced into the contract."""


def specialist_prompt(kind: SpecialistKind) -> str:
    vocabulary = ", ".join(DISEASE_VOCABULARY[kind])
    focus = {
        SpecialistKind.SKIN: (
            "the skin, lips, nostrils, face and neck. Look for: crusted scabs "
            "around lips/nostrils (orf), pox nodules or pustules (goat pox), "
            "circular hairless scaly patches (ringworm), scabs with hair loss "
            "and itching (mange), abscess swellings under jaw/neck "
            "(caseous lymphadenitis), open wounds"
        ),
        SpecialistKind.EYE: (
            "the eyes and eyelids. Look for: cloudy or red watery eyes "
            "(infectious keratoconjunctivitis / pinkeye), very pale inner "
            "eyelid membranes (anemia, FAMACHA score 4-5), eye injuries"
        ),
        SpecialistKind.HOOF: (
            "the hooves and legs. Look for: inflamed smelly tissue between "
            "hooves (foot rot), blisters on hooves or coronary band "
            "(suspect FMD — report immediately), limping posture, overgrown "
            "hooves"
        ),
        SpecialistKind.UDDER: (
            "the udder and teats. Look for: swollen red hot udder or clotted "
            "milk signs (clinical mastitis), wounds or injuries"
        ),
        SpecialistKind.GENERAL: (
            "the whole animal. Look for: ribs/spine clearly visible "
            "(undercondition), stained hind legs (diarrhea), drooping head "
            "or ears, isolation from the herd (lethargy)"
        ),
    }[kind]
    return f"""You are a veterinary specialist examining one goat photograph.
Focus ONLY on {focus}.
Report ONLY conditions actually VISIBLE in this image; do not speculate
about internal diseases.
Use disease codes from exactly this list: {vocabulary}.
confidence is 0.0-1.0. severity is mild, moderate or severe.

Answer with ONLY a JSON object, no other text, in exactly this shape:
{{
  "conditions": [
    {{"disease": "CODE", "confidence": 0.0-1.0, "severity": "mild|moderate|severe",
      "note": "one sentence on what you see"}}
  ]
}}
Use an empty "conditions" list if the photo shows nothing abnormal in your
focus area."""


def parse_specialist_response(text: str, kind: SpecialistKind) -> SpecialistResponse:
    """Validate and vocabulary-coerce a specialist answer; unknown disease
    codes become OTHER with the original guess kept in the note."""
    try:
        raw = json.loads(_extract_json_object(text))
        # {"conditions": null} is a plausible "nothing visible" answer and
        # parses as empty; a non-object answer or a present-but-non-list
        # value becomes SpecialistParseError, which run_specialist maps to
        # ProviderResponseError so the image row follows its normal
        # error/retry path instead of crashing the cycle.
        items = answer_list(raw, "conditions")
    except (ValueError, GateParseError) as exc:
        raise SpecialistParseError(f"no valid JSON in specialist answer: {exc}") from exc
    vocabulary = DISEASE_VOCABULARY[kind]
    conditions: list[SpecialistCondition] = []
    for item in items[:MAX_SPECIALIST_CONDITIONS]:
        try:
            condition = SpecialistCondition.model_validate(item)
        except ValueError:
            continue  # a malformed condition is dropped, not fatal
        if condition.disease not in vocabulary:
            note = f"(model said: {condition.disease}) " + (condition.note or "")
            conditions.append(condition.model_copy(update={"disease": "OTHER", "note": note[:500]}))
        else:
            conditions.append(condition)
    return SpecialistResponse(conditions=conditions)


@dataclass(frozen=True)
class SpecialistCallResult:
    """One specialist invocation's parsed outcome for the audit row."""

    response: SpecialistResponse
    provider: str
    model: str
    prompt_version: str
    latency_ms: int
    raw_text: str


async def run_specialist(
    provider: VisionProvider, image_jpeg: bytes, kind: SpecialistKind
) -> SpecialistCallResult:
    prompt = specialist_prompt(kind)
    answer: ProviderAnswer = await provider.complete(image_jpeg, prompt)
    try:
        response = parse_specialist_response(answer.text, kind)
    except SpecialistParseError as exc:
        raise ProviderResponseError(
            f"{provider.name} answer failed the {kind.value} contract: {exc}"
        ) from exc
    return SpecialistCallResult(
        response=response,
        provider=answer.provider,
        model=answer.model,
        prompt_version=SPECIALIST_PROMPT_VERSIONS[kind],
        latency_ms=answer.latency_ms,
        raw_text=answer.text[:2000],
    )
