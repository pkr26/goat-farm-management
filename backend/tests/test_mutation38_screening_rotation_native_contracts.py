"""Genuine native gate outcomes retain configured second-opinion ordering."""

import datetime as dt

import pytest

from app.services.screening.providers import ProviderError
from app.services.screening.rotation import ProviderRotation

from .test_screening import CountingProvider, _jpeg_bytes


@pytest.mark.parametrize("explicit_backup", [False, True], ids=["day-primary", "native-backup"])
async def test_native_served_gate_chooses_the_next_usable_configured_second_opinion(
    explicit_backup: bool,
) -> None:
    providers = [CountingProvider(name=name) for name in ("a", "b", "c")]
    try:
        rotation = ProviderRotation(providers)
    except ValueError as exc:
        if str(exc) != "ProviderRotation needs at least one provider":
            raise
        pytest.fail(f"Three configured native providers must form a usable rotation: {exc}")
    # This is a genuine date supplied through the exported native interface.
    # Choose an ordinal whose declared day primary is configured provider A.
    on = dt.date.fromordinal(dt.date(2026, 9, 15).toordinal() // 3 * 3)
    assert rotation.primary_for(on) is providers[0]
    actual_primary = providers[int(explicit_backup)]
    try:
        outcome = await rotation.gate_with_fallback(actual_primary, _jpeg_bytes(160, 80))
    except ProviderError as exc:
        pytest.fail(f"The genuinely successful configured gate must remain available: {exc}")
    assert outcome.served_by == actual_primary.name and outcome.failed_providers == ()
    assert outcome.result.response.flagged
    second = rotation.cross_checker_for(on, outcome.served_by, outcome.failed_providers)
    assert second is providers[2 if explicit_backup else 1]
    assert second.name != outcome.served_by
    assert actual_primary.calls == 1
    assert all(provider.calls == 0 for provider in providers if provider is not actual_primary)


def test_empty_native_provider_configuration_has_no_rotation() -> None:
    with pytest.raises(ValueError, match="needs at least one provider"):
        ProviderRotation([])
