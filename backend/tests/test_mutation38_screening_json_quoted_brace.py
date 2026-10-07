"""Visible clinical text may contain a closing brace without closing its JSON object."""

import json

import pytest

from app.services.screening.gate import GateParseError, _extract_json_object, parse_gate_response


def test_closing_brace_in_plain_clinical_note_does_not_end_the_provider_object() -> None:
    note = 'Visible raised scab shaped like } near the lip; quoted "mouth" in the camera label'
    document = json.dumps(
        {
            "flagged": True,
            "confidence": 0.8,
            "observations": [
                {"region": "mouth", "label": "Raised lip scab", "confidence": 0.75, "note": note}
            ],
        }
    )
    wrapped = "Provider explanation: " + document + "```"
    try:
        extracted = _extract_json_object(wrapped)
        reply = parse_gate_response(wrapped)
    except GateParseError as exc:
        pytest.fail(
            f"A legal clinical note string cannot terminate its surrounding JSON object: {exc}"
        )
    assert extracted == document
    assert reply.flagged and len(reply.observations) == 1
    assert reply.observations[0].note == note
