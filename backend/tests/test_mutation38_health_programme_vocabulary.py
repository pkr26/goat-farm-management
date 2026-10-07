"""Programme recognition uses complete aliases and ordinary title wrappers."""

import pytest

from app.services.health import _has_alias, protocol_phrase_of, template_names_for_task


@pytest.mark.parametrize(
    ("words", "aliases", "expected"),
    [
        (("annual", "ppr", "round"), ("ppr",), True),
        (("foot", "and", "mouth"), ("foot and mouth",), True),
        (("pprtraders", "exam"), ("ppr",), False),
        (("annual", "exam"), ("", "   "), False),
    ],
    ids=["whole-word", "whole-phrase", "substring-is-not-an-alias", "blank-is-not-an-alias"],
)
def test_native_health_aliases_require_the_declared_complete_nonblank_words(
    words: tuple[str, ...], aliases: tuple[str, ...], expected: bool
) -> None:
    assert _has_alias(words, *aliases) is expected


@pytest.mark.parametrize(
    ("title", "phrase", "templates"),
    [
        ("[unfinished supplier", "[unfinished supplier", ()),
        ("[PPR", "[PPR", ("PPR",)),
        ("[Batch #7] FMD vaccination", "FMD vaccination", ("FMD",)),
        (
            "[PPR] extra supplier] ET + HS pre-monsoon round",
            "ET + HS pre-monsoon round",
            ("Enterotoxaemia (ET)", "Haemorrhagic Septicaemia (HS)"),
        ),
    ],
    ids=["unclosed-unknown", "unclosed-known", "ordinary-prefix", "supplier-containing-bracket"],
)
def test_native_health_title_wrapper_retains_the_actual_protocol_phrase(
    title: str, phrase: str, templates: tuple[str, ...]
) -> None:
    assert protocol_phrase_of(title) == phrase
    assert template_names_for_task(title, "VACCINE") == templates
