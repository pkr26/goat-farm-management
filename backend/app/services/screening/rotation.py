"""Date-based round-robin over the configured providers.

The day's ordinal picks the primary — ``providers[date.toordinal() % len]``
— so "Monday Claude, Tuesday GLM" falls out of the list order with zero
stored state and identical results on every replica. The next entry in the
list is the cross-checker for flagged photos and the first fallback when
the primary errors.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from dataclasses import dataclass

from .providers import GateCallResult, ProviderError, VisionProvider
from .providers import gate as run_gate


@dataclass(frozen=True)
class GateOutcome:
    """A gate verdict plus who actually served it — the fallback chain may
    have skipped a failed primary, and the audit row must record that."""

    result: GateCallResult
    served_by: str
    failed_providers: tuple[str, ...]


class ProviderRotation:
    def __init__(self, providers: list[VisionProvider]) -> None:
        if not providers:
            raise ValueError("ProviderRotation needs at least one provider")
        self._providers = providers

    def __len__(self) -> int:
        return len(self._providers)

    def __iter__(self) -> Iterator[VisionProvider]:
        return iter(self._providers)

    def provider_named(self, name: str) -> VisionProvider | None:
        """Resolve a provider by its rotation name (e.g. whoever actually
        served a fallback gate call); None when absent."""
        for provider in self._providers:
            if provider.name == name:
                return provider
        return None

    def names(self) -> list[str]:
        return [provider.name for provider in self._providers]

    def primary_for(self, on: dt.date) -> VisionProvider:
        return self._providers[on.toordinal() % len(self._providers)]

    def secondary_for(self, on: dt.date) -> VisionProvider | None:
        """The flagged-image cross-checker; None when only one provider is
        configured (Phase 1 deployments keep their single-call behavior)."""
        if len(self._providers) < 2:
            return None
        return self._providers[(on.toordinal() + 1) % len(self._providers)]

    def cross_checker_for(
        self, on: dt.date, served_by: str, failed_providers: tuple[str, ...] = ()
    ) -> VisionProvider | None:
        """The flagged-image second opinion, skipping unusable providers.

        ``secondary_for`` blindly picks primary+1 by date, so in a 3-provider
        rotation whose primary failed (fallback B served the gate) the
        "cross-checker" was B itself and the second opinion was silently
        skipped (P3, 2026-09-20 audit). This variant walks the rotation from
        the day's primary and returns the first provider that neither served
        the gate nor already failed it; None when every provider is excluded
        or only one is configured.
        """
        if len(self._providers) < 2:
            return None
        excluded = {served_by, *failed_providers}
        for step in range(1, len(self._providers)):
            candidate = self._providers[(on.toordinal() + step) % len(self._providers)]
            if candidate.name not in excluded:
                return candidate
        return None

    def fallbacks_for(self, primary: VisionProvider) -> list[VisionProvider]:
        """Every other provider, rotation order preserved, starting after
        the primary — the queue a failing primary drains through."""
        if len(self._providers) < 2:
            return []
        start = self._providers.index(primary)
        return [
            self._providers[(start + step) % len(self._providers)]
            for step in range(1, len(self._providers))
        ]

    async def gate_with_fallback(self, primary: VisionProvider, image_jpeg: bytes) -> GateOutcome:
        """Try the primary, then each fallback once. The last error wins if
        every provider fails."""
        chain = [primary, *self.fallbacks_for(primary)]
        failures: list[str] = []
        last_error: ProviderError | None = None
        for provider in chain:
            try:
                result = await run_gate(provider, image_jpeg)
            except ProviderError as exc:
                failures.append(provider.name)
                last_error = exc
                continue
            return GateOutcome(
                result=result, served_by=provider.name, failed_providers=tuple(failures)
            )
        if last_error is None:
            # ``chain`` always has primary. This is defensive for a custom
            # provider/adapter that violates the normal return-or-ProviderError
            # contract; assertions disappear under ``python -O``.
            last_error = ProviderError("gate provider chain ended without a result")
        raise GateExhaustedError(tuple(failures), last_error)


class GateExhaustedError(ProviderError):
    """Every provider in the rotation failed; the message aggregates the
    per-provider causes for the audit row."""

    def __init__(self, failed_providers: tuple[str, ...], cause: ProviderError) -> None:
        super().__init__(f"all screening providers failed ({', '.join(failed_providers)}): {cause}")
        self.failed_providers = failed_providers
