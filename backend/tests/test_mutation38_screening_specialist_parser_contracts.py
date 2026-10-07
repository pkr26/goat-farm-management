"""Native specialist answers conserve bounded, reportable clinical evidence."""

import json

import pytest

from app.services.screening.specialists import (
    SpecialistKind,
    SpecialistParseError,
    SpecialistResponse,
    parse_specialist_response,
)


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """The native parser has no application database lifecycle."""


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """Pure response parsing does not truncate application records."""


def _parse_valid(disease: str, note: str | None, severity: str = "moderate") -> SpecialistResponse:
    answer = json.dumps(
        {
            "conditions": [
                {"disease": disease, "confidence": 0.9, "severity": severity, "note": note}
            ]
        }
    )
    try:
        return parse_specialist_response(answer, SpecialistKind.SKIN)
    except (SpecialistParseError, TypeError, ValueError) as exc:
        pytest.fail(f"A valid specialist assessment must retain its clinical evidence: {exc}")


def test_specialist_answer_retains_the_first_five_valid_conditions() -> None:
    diseases = ["ORF", "WOUND", "MANGE", "RINGWORM", "GOAT_POX", "CASEOUS_LYMPHADENITIS"]
    answer = json.dumps(
        {
            "conditions": [
                {
                    "disease": disease,
                    "confidence": 0.9,
                    "severity": "moderate",
                    "note": f"Visible assessment {index}",
                }
                for index, disease in enumerate(diseases, 1)
            ]
        }
    )
    try:
        parsed = parse_specialist_response(answer, SpecialistKind.SKIN)
    except (SpecialistParseError, TypeError, ValueError) as exc:
        pytest.fail(f"A valid bounded specialist answer must remain parseable: {exc}")
    assert [condition.disease for condition in parsed.conditions] == diseases[:5]
    assert [condition.note for condition in parsed.conditions] == [
        f"Visible assessment {index}" for index in range(1, 6)
    ]


def test_specialist_trims_provider_whitespace_before_vocabulary_and_severity_validation() -> None:
    parsed = _parse_valid("  orf \t", " \n Visible crust near lips \t ", " mild ")
    condition = parsed.conditions[0]
    assert condition.disease == "ORF" and condition.severity == "mild"
    assert condition.note == "Visible crust near lips"
    assert condition.confidence == 0.9


@pytest.mark.parametrize("length", [1, 2, 79, 80, 81], ids=["one", "two", "79", "80", "81"])
def test_specialist_disease_guess_boundaries_preserve_valid_original_codes(length: int) -> None:
    disease = "X" * length
    if 2 <= length <= 80:
        condition = _parse_valid(disease, None).conditions[0]
        assert condition.disease == "OTHER"
        assert condition.note == f"(model said: {disease}) "
    else:
        answer = json.dumps({"conditions": [{"disease": disease, "confidence": 0.9}]})
        with pytest.raises(SpecialistParseError, match="invalid condition"):
            parse_specialist_response(answer, SpecialistKind.SKIN)


@pytest.mark.parametrize("length", [None, 499, 500, 501], ids=["absent", "499", "500", "501"])
def test_specialist_known_disease_note_preserves_the_declared_boundary(length: int | None) -> None:
    note = None if length is None else "n" * length
    if length is None or length <= 500:
        condition = _parse_valid("ORF", note).conditions[0]
        assert condition.disease == "ORF" and condition.note == note
    else:
        answer = json.dumps({"conditions": [{"disease": "ORF", "confidence": 0.9, "note": note}]})
        with pytest.raises(SpecialistParseError, match="invalid condition"):
            parse_specialist_response(answer, SpecialistKind.SKIN)


@pytest.mark.parametrize("length", [None, 499, 500], ids=["absent", "499", "500"])
def test_unknown_specialist_disease_keeps_the_original_guess_and_bounded_note(
    length: int | None,
) -> None:
    note = None if length is None else "n" * length
    condition = _parse_valid("UNLISTED_VISIBLE_CONDITION", note).conditions[0]
    assert condition.disease == "OTHER"
    expected = "(model said: UNLISTED_VISIBLE_CONDITION) " + (note or "")
    assert condition.note == expected[:500]
    assert condition.confidence == 0.9 and condition.severity == "moderate"
