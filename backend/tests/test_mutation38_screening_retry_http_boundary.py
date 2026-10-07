"""Actual gateway status500 gets one durably admitted retry; status499 does not."""

import httpx
import pytest

from app.services.screening.providers import BudgetedProvider, ProviderError

from .test_mutation38_screening_adapter_wire_contracts import _adapter, _answer_payload
from .test_screening import _jpeg_bytes, _provider_settings


@pytest.mark.parametrize("vendor", ["anthropic", "openai"])
@pytest.mark.parametrize(
    "first_status", [499, 500], ids=["last-client-error", "first-server-error"]
)
async def test_transient_http_boundary_preserves_exact_paid_retry_admission(
    vendor: str, first_status: int
) -> None:
    requests = 0
    admitted = 0

    def route(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            return httpx.Response(
                first_status, json={"error": "actual controlled gateway response"}
            )
        return httpx.Response(200, json=_answer_payload(vendor))

    async def admission() -> None:
        nonlocal admitted
        admitted += 1

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        provider = _adapter(vendor)(_provider_settings(), client=client)
        budgeted = BudgetedProvider(provider, admission)
        if first_status == 499:
            with pytest.raises(ProviderError):
                await budgeted.complete(_jpeg_bytes(80, 160), "native gate prompt")
            assert requests == admitted == 1
        else:
            try:
                reply = await budgeted.complete(_jpeg_bytes(80, 160), "native gate prompt")
            except ProviderError as exc:
                pytest.fail(
                    f"The first gateway server error must admit its one successful retry: {exc}"
                )
            assert reply.text == "native answer"
            assert requests == admitted == 2
        await provider.aclose()
        assert not client.is_closed
