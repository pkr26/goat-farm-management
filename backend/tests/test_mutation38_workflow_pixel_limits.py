"""Independent camera decode limits and provider exhaustion audit contracts.

The limits are literal policy boundaries and the provider failures have
distinct messages so an earlier cause cannot substitute for the final
attempted provider's failure.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.services.screening.images import ImageNormalizationError, normalize_image
from app.services.screening.rotation import GateExhaustedError, ProviderRotation

from .test_screening import CountingProvider, _jpeg_bytes


def _uniform_png(width: int, height: int) -> bytes:
    # The supported grayscale camera encoding keeps fixture allocation small;
    # the decoder must still account for every declared raster pixel.
    source = io.BytesIO()
    with Image.new("L", (width, height), 137) as image:
        image.save(source, format="PNG")
    return source.getvalue()


def test_decode_accepts_exactly_twenty_five_million_pixels() -> None:
    try:
        evidence = normalize_image(_uniform_png(5_000, 5_000), 64, "image/png")
    except ImageNormalizationError as exc:
        pytest.fail(f"A supported camera raster at the 25 MP decode limit was rejected: {exc}")

    assert (evidence.width, evidence.height) == (64, 64)
    with Image.open(io.BytesIO(evidence.data)) as decoded:
        assert decoded.format == "JPEG"
        assert decoded.size == (64, 64)
        assert decoded.convert("RGB").getpixel((32, 32)) == (137, 137, 137)


def test_decode_rejects_the_first_pixel_past_twenty_five_million() -> None:
    # 4901*5101 = 25,000,001. Both edges remain below the independent
    # 10,000-pixel edge budget, isolating the total decoded-pixel limit.
    source = _uniform_png(4_901, 5_101)
    with pytest.raises(ImageNormalizationError, match=r"(?:pixel|dimension) budget"):
        normalize_image(source, 64, "image/png")


async def test_provider_exhaustion_reports_the_final_actual_provider_cause() -> None:
    providers = [CountingProvider(name=name, fail=True) for name in ("alpha", "beta", "gamma")]
    rotation = ProviderRotation(providers)

    with pytest.raises(GateExhaustedError) as exhausted:
        await rotation.gate_with_fallback(providers[0], _jpeg_bytes(100, 50))

    assert exhausted.value.failed_providers == ("alpha", "beta", "gamma")
    assert [provider.calls for provider in providers] == [1, 1, 1]
    assert str(exhausted.value) == (
        "all screening providers failed (alpha, beta, gamma): gamma outage"
    )
