"""Actual httpx wire/ownership and elapsed-ms contracts, with bounded local transport."""

import asyncio
import base64
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import ScreeningRotationProvider
from app.services.screening import providers as adapters
from app.services.screening.providers import (
    AnthropicProvider,
    OpenAICompatibleProvider,
    ProviderError,
    build_provider_rotation,
)

from .test_screening import _jpeg_bytes, _provider_settings

Adapter = AnthropicProvider | OpenAICompatibleProvider
AdapterClass = type[AnthropicProvider] | type[OpenAICompatibleProvider]


def _adapter(vendor: str) -> AdapterClass:
    return AnthropicProvider if vendor == "anthropic" else OpenAICompatibleProvider


def _answer_payload(vendor: str) -> dict[str, object]:
    if vendor == "anthropic":
        return {
            "content": [
                {"type": "text", "text": "native "},
                "gateway non-text metadata",
                {"type": "image", "text": "not an answer"},
                {"type": "text", "text": "answer"},
            ]
        }
    return {
        "choices": [
            {"message": {"content": "native answer"}},
            {"message": {"content": "an unselected alternative"}},
        ]
    }


def _assert_wire_image(body: dict[str, Any], vendor: str, jpeg: bytes, prompt: str) -> None:
    assert body["messages"][-1]["role"] == "user"
    if vendor == "anthropic":
        assert body["system"] == prompt
        encoded = body["messages"][0]["content"][0]["source"]["data"]
        assert base64.b64decode(encoded) == jpeg
    else:
        assert body["messages"][0] == {"role": "system", "content": prompt}
        encoded = body["messages"][1]["content"][1]["image_url"]["url"]
        assert encoded == "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")


@pytest.mark.parametrize("vendor", ["anthropic", "openai"])
def test_native_adapter_preserves_caller_transport_answer_and_elapsed_milliseconds(
    monkeypatch: pytest.MonkeyPatch, vendor: str
) -> None:
    jpeg = _jpeg_bytes(20, 20)
    requests: list[httpx.Request] = []

    def route(request: httpx.Request) -> httpx.Response:
        # The supplied real client's route/auth context is part of the
        # dependency contract. A newly allocated default transport lacks it.
        if request.headers.get("x-native-clinic-route") != "clinic-a":
            return httpx.Response(403, json={"error": "missing caller route context"})
        requests.append(request)
        body = json.loads(request.content)
        _assert_wire_image(body, vendor, jpeg, "native stage prompt")
        return httpx.Response(200, json=_answer_payload(vendor))

    real_client = httpx.AsyncClient
    allocated: list[httpx.AsyncClient] = []

    def bounded_default_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(route)
        created = real_client(*args, **kwargs)
        allocated.append(created)
        return created

    async def call() -> None:
        client = real_client(
            transport=httpx.MockTransport(route), headers={"x-native-clinic-route": "clinic-a"}
        )
        monkeypatch.setattr(httpx, "AsyncClient", bounded_default_client)
        elapsed = iter((100.0, 101.125))
        monkeypatch.setattr(adapters, "time", SimpleNamespace(monotonic=lambda: next(elapsed)))
        try:
            try:
                provider = _adapter(vendor)(_provider_settings(), client=client)
                await provider.aclose()
                assert not client.is_closed, "A caller-owned transport must remain usable"
                answer = await provider.complete(jpeg, "native stage prompt")
            except (ProviderError, AttributeError, TypeError) as error:
                pytest.fail(f"A valid configured native adapter call must complete: {error}")
            assert answer.text == "native answer"
            assert answer.provider == (
                "anthropic" if vendor == "anthropic" else "openai_compatible"
            )
            assert answer.latency_ms == 1125, (
                "1.125 elapsed seconds must be reported in milliseconds"
            )
            assert len(requests) == 1
        finally:
            await client.aclose()
            for extra in allocated:
                await extra.aclose()

    asyncio.run(call())


@pytest.mark.parametrize("vendor", ["anthropic", "openai"])
@pytest.mark.parametrize("override", [False, True])
def test_native_rotation_entry_honors_its_endpoint_or_the_configured_fallback(
    vendor: str, override: bool
) -> None:
    settings = _provider_settings().model_copy(
        update={
            "screening_anthropic_api_key": None,
            "screening_openai_api_key": None,
            "screening_anthropic_base_url": "https://default-anthropic.example.test",
            "screening_openai_base_url": "https://default-openai.example.test/v1",
        }
    )
    base = (
        "https://rotated-clinic.example.test/v1"
        if override
        else (
            settings.screening_anthropic_base_url
            if vendor == "anthropic"
            else settings.screening_openai_base_url
        )
    )
    endpoint = base + ("/v1/messages" if vendor == "anthropic" else "/chat/completions")
    entry = ScreeningRotationProvider(
        kind="anthropic" if vendor == "anthropic" else "openai_compatible",
        name=f"native-{vendor}",
        model=f"native-{vendor}-model",
        api_key=SecretStr("rotation-test-key"),
        base_url=base if override else None,
    )
    routes: list[str] = []

    def route(request: httpx.Request) -> httpx.Response:
        routes.append(str(request.url))
        if str(request.url) != endpoint:
            return httpx.Response(404, json={"error": "wrong configured provider route"})
        assert json.loads(request.content)["model"] == entry.model
        return httpx.Response(200, json=_answer_payload(vendor))

    async def call() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
            try:
                provider = _adapter(vendor)(
                    settings, name=entry.name, rotation_entry=entry, client=client
                )
                answer = await provider.complete(_jpeg_bytes(20, 20), "native prompt")
            except (ProviderError, AttributeError, TypeError) as error:
                pytest.fail(f"A valid native rotation entry must retain its route: {error}")
            assert (answer.provider, answer.model, answer.text) == (
                entry.name,
                entry.model,
                "native answer",
            )
            await provider.aclose()
            assert not client.is_closed
        assert routes == [endpoint]

    asyncio.run(call())


@pytest.mark.parametrize("mode", ["single-anthropic", "single-openai", "rotation"])
def test_configured_native_provider_builder_preserves_vendor_wire_and_rotation_order(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    settings = _provider_settings()
    settings.screening_anthropic_base_url = "https://builder-anthropic.example.test"
    settings.screening_openai_base_url = "https://builder-openai.example.test/v1"
    settings.screening_anthropic_model = "builder-anthropic-model"
    settings.screening_openai_model = "builder-openai-model"
    settings.screening_provider = "openai_compatible" if mode == "single-openai" else "anthropic"
    expected: list[tuple[str, str, str]]
    if mode == "rotation":
        settings.screening_provider_rotation = [
            ScreeningRotationProvider(
                kind="openai_compatible",
                name="rotated-openai",
                model="builder-openai-model",
                api_key=SecretStr("openai-test-key"),
                base_url=settings.screening_openai_base_url,
            ),
            ScreeningRotationProvider(
                kind="anthropic",
                name="rotated-anthropic",
                model="builder-anthropic-model",
                api_key=SecretStr("anthropic-test-key"),
                base_url=settings.screening_anthropic_base_url,
            ),
        ]
        expected = [
            ("rotated-openai", "builder-openai-model", "openai"),
            ("rotated-anthropic", "builder-anthropic-model", "anthropic"),
        ]
    else:
        vendor = "openai" if mode == "single-openai" else "anthropic"
        expected = [
            (
                "anthropic" if vendor == "anthropic" else "openai_compatible",
                f"builder-{vendor}-model",
                vendor,
            )
        ]
    observed: list[str] = []

    def route(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        vendor = "anthropic" if body["model"] == "builder-anthropic-model" else "openai"
        endpoint = (
            settings.screening_anthropic_base_url + "/v1/messages"
            if vendor == "anthropic"
            else settings.screening_openai_base_url + "/chat/completions"
        )
        if str(request.url) != endpoint:
            return httpx.Response(404, json={"error": "vendor transport format mismatch"})
        observed.append(vendor)
        _assert_wire_image(body, vendor, _jpeg_bytes(20, 20), "shared stage prompt")
        return httpx.Response(200, json=_answer_payload(vendor))

    real_client = httpx.AsyncClient
    allocated: list[httpx.AsyncClient] = []

    def bounded_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(route)
        created = real_client(*args, **kwargs)
        allocated.append(created)
        return created

    async def call() -> None:
        monkeypatch.setattr(httpx, "AsyncClient", bounded_client)
        try:
            try:
                providers = build_provider_rotation(settings)
                answers = [
                    await provider.complete(_jpeg_bytes(20, 20), "shared stage prompt")
                    for provider in providers
                ]
            except (ProviderError, AttributeError, TypeError) as error:
                pytest.fail(
                    f"A valid configured provider list must use each vendor format: {error}"
                )
            assert [(answer.provider, answer.model) for answer in answers] == [
                (name, model) for name, model, _ in expected
            ]
            assert all(answer.text == "native answer" for answer in answers)
            assert observed == [vendor for _, _, vendor in expected]
        finally:
            for client in allocated:
                await client.aclose()

    asyncio.run(call())
