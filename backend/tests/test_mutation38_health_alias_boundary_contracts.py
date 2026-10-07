"""Plain title and alias inputs keep native programme recognition exact."""

import pytest

from app.services.health import _has_alias, protocol_phrase_of, template_names_for_task


@pytest.mark.parametrize("blank", ["", "   "], ids=["empty", "whitespace"])
def test_blank_alias_does_not_hide_a_later_complete_alias(blank: str) -> None:
    assert _has_alias(("annual", "ppr"), blank, "ppr") is True


@pytest.mark.parametrize(
    ("words", "aliases", "expected"),
    [
        (("annual", "ppr"), ("ppr",), True),
        (("foot", "and", "mouth"), ("foot and mouth",), True),
        (("pprtraders",), ("ppr",), False),
        (("annual",), ("annual ppr programme",), False),
    ],
    ids=["terminal-word", "complete-phrase", "substring", "longer-alias"],
)
def test_complete_alias_windows_preserve_exact_phrase_matching(
    words: tuple[str, ...], aliases: tuple[str, ...], expected: bool
) -> None:
    assert _has_alias(words, *aliases) is expected


@pytest.mark.parametrize(
    ("title", "phrase"),
    [
        ("[Batch #7]PPR", "PPR"),
        ("[]PPR", "PPR"),
        ("[supplier] FMD vaccination", "FMD vaccination"),
        ("[unfinished PPR", "[unfinished PPR"),
    ],
    ids=["no-gap", "empty-wrapper", "spaced-wrapper", "unclosed-wrapper"],
)
def test_native_title_prefix_preserves_the_entire_plain_protocol_phrase(
    title: str, phrase: str
) -> None:
    assert protocol_phrase_of(title) == phrase


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Haemorrhagic Septicaemia", ("Haemorrhagic Septicaemia (HS)",)),
        ("Enterotoxaemia ET + HS", ("Enterotoxaemia (ET)", "Haemorrhagic Septicaemia (HS)")),
    ],
    ids=["hs-only", "combined-et-hs"],
)
def test_combined_round_requires_both_declared_component_aliases(
    title: str, expected: tuple[str, ...]
) -> None:
    assert template_names_for_task(title, "VACCINE") == expected
