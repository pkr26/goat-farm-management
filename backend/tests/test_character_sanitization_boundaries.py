"""Consecutive line breaks cannot forge spacing or sections in exported titles."""

import pytest

from app.characters import sanitize_single_line


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """Character normalization has no database dependency."""


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """Character normalization opens no integration tables."""


def test_consecutive_export_title_breaks_collapse_to_one_separator() -> None:
    assert sanitize_single_line("  Herd\r\n\r\n\u2028\u2029plan  ") == "Herd plan"
