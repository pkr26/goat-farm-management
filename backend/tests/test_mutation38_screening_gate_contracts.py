"""Native provider gate replies conserve bounded clinical observations and JSON framing."""

import json
from typing import Any

import pytest

from app.services.screening.gate import GateParseError, _extract_json_object, parse_gate_response


@pytest.mark.parametrize(
    ("field", "value", "accepted"),
    [
        ("region", "", False),
        ("region", "r", True),
        ("region", "r" * 40, True),
        ("region", "r" * 41, False),
        ("label", "", False),
        ("label", "l", True),
        ("label", "l" * 80, True),
        ("label", "l" * 81, False),
        ("note", None, True),
        ("note", "n" * 500, True),
        ("note", "n" * 501, False),
    ],
    ids=[
        "region-empty",
        "region-first",
        "region-maximum",
        "region-overlong",
        "label-empty",
        "label-first",
        "label-maximum",
        "label-overlong",
        "note-absent",
        "note-maximum",
        "note-overlong",
    ],
)
def test_native_gate_observation_preserves_literal_text_bands(
    field: str, value: str | None, accepted: bool
) -> None:
    observation = {
        "region": "mouth",
        "label": "Visible crusted lips",
        "confidence": 0.75,
        "note": None,
    }
    observation[field] = value
    body = json.dumps({"flagged": True, "confidence": 0.8, "observations": [observation]})
    if not accepted:
        with pytest.raises(GateParseError):
            parse_gate_response(body)
        return
    try:
        reply = parse_gate_response(body)
    except GateParseError as exc:
        pytest.fail(
            f"A supported native gate observation must retain its bounded visible evidence: {exc}"
        )
    assert len(reply.observations) == 1
    assert getattr(reply.observations[0], field) == value


@pytest.mark.parametrize("count", [8, 9], ids=["eight-visible-signs", "overfull"])
def test_one_gate_reply_contains_at_most_eight_visible_observations(count: int) -> None:
    body = json.dumps(
        {
            "flagged": True,
            "confidence": 0.8,
            "observations": [
                {"region": "mouth", "label": f"Visible sign {i}", "confidence": 0.75}
                for i in range(count)
            ],
        }
    )
    if count == 9:
        with pytest.raises(GateParseError):
            parse_gate_response(body)
        return
    try:
        reply = parse_gate_response(body)
    except GateParseError as exc:
        pytest.fail(
            f"The native gate's eighth visible sign remains usable clinical evidence: {exc}"
        )
    assert [item.label for item in reply.observations] == [f"Visible sign {i}" for i in range(8)]


def test_native_gate_trims_device_text_and_defaults_to_assessable_quality() -> None:
    body = json.dumps(
        {
            "flagged": True,
            "confidence": 0.8,
            "observations": [
                {
                    "region": " mouth ",
                    "label": " Crusted lips ",
                    "confidence": 0.75,
                    "note": " Visible scab ",
                }
            ],
        }
    )
    try:
        reply = parse_gate_response(body)
    except GateParseError as exc:
        pytest.fail(f"A valid ordinary provider reply must normalize clinical text: {exc}")
    assert not reply.quality_problem
    assert (
        reply.observations[0].region,
        reply.observations[0].label,
        reply.observations[0].note,
    ) == ("mouth", "Crusted lips", "Visible scab")


@pytest.mark.parametrize(
    "extra",
    [
        {"": "opaque provider metadata"},
        {'quoted"field': "opaque metadata"},
        {"path": 'C:\\barn\\pen; braces {left} and an escaped "quote"'},
    ],
    ids=["empty-json-member", "quoted-json-member", "escaped-value"],
)
def test_balanced_json_extraction_preserves_plain_json_strings_before_adjacent_suffix(
    extra: dict[str, Any],
) -> None:
    # Native JSON allows these ordinary string keys/values. Extra provider
    # metadata is ignored by the declared gate response adapter, while the
    # shared balanced-object extractor must preserve it until validation.
    document = json.dumps({**extra, "flagged": False, "confidence": 0.9, "observations": []})
    wrapped = "provider preface: " + document + "``` trailing commentary"
    try:
        extracted = _extract_json_object(wrapped)
        reply = parse_gate_response(wrapped)
    except GateParseError as exc:
        pytest.fail(
            f"A complete balanced native JSON object must survive its plain-string framing: {exc}"
        )
    assert extracted == document
    assert not reply.flagged and not reply.quality_problem and reply.observations == []
