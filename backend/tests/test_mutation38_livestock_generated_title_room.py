"""Native generated titles retain operational text at the literal storage limit."""

import pytest

from app.services.breeding import _fit_generated_title


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """The declared native string-fitting interface does not perform SQL."""


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """Each case owns its operational template and original animal tag."""


@pytest.mark.parametrize(
    ("room", "retained_tag"),
    [(0, ""), (1, "D"), (50, "DOE-NATIVE-TEMPLATE")],
    ids=["operational-text-fills-storage", "one-tag-character", "whole-tag"],
)
def test_native_title_fit_preserves_operational_text_when_tag_room_runs_out(
    room: int, retained_tag: str
) -> None:
    prefix = "Birthing kit check: "
    checklist = (
        " — prepare iodine, clean towels, disinfected scissors, lubricant, gloves, "
        "thermometer, colostrum supplies, ear tags, weighing sling and kidding notes. "
    )
    # This is a valid template supplied to the typed native helper. The
    # storage budget is the published literal200-character title limit;
    # current API templates happen to leave additional tag room.
    suffix = (checklist * 2)[: 200 - len(prefix) - room]
    assert len(prefix + suffix) == 200 - room
    fitted = _fit_generated_title(prefix, "DOE-NATIVE-TEMPLATE", suffix)
    assert fitted == prefix + retained_tag + suffix
    assert len(fitted) <= 200
