"""Mutation-campaign gap pins for health-event ledger note formatting (2026-09-30).

`_health_transaction_note` builds the human-readable Transaction note; the
label fallback chain and the singular/plural boundary both mutated freely.
Pure function: pinned exactly.
"""

from app.services.health import _health_transaction_note


def test_note_label_falls_back_product_then_disease_then_type() -> None:
    assert _health_transaction_note("TREATMENT", "Ivermectin", "", 2) == "Ivermectin for 2 animals"
    assert _health_transaction_note("TREATMENT", "", "PPR", 2) == "PPR for 2 animals"
    assert _health_transaction_note("VACCINATION", "", "", 1) == "VACCINATION for 1 animal"


def test_note_blank_label_falls_through_to_event_type() -> None:
    # A whitespace-only product name is not a label: the stripped result is
    # empty, so the event type is used.
    assert _health_transaction_note("TREATMENT", "   ", "PPR", 1) == "TREATMENT for 1 animal"
    assert _health_transaction_note("TREATMENT", "", "  ", 3) == "TREATMENT for 3 animals"


def test_note_singular_plural_boundary() -> None:
    assert _health_transaction_note("TREATMENT", "Ivermectin", "", 1) == "Ivermectin for 1 animal"
    assert _health_transaction_note("TREATMENT", "Ivermectin", "", 2) == "Ivermectin for 2 animals"
    assert _health_transaction_note("TREATMENT", "Ivermectin", "", 0) == "Ivermectin for 0 animals"
