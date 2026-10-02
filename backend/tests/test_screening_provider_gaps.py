"""Mutation-campaign gap tests for screening providers (2026-09-30).

Surviving mutants: the transport-ownership flag (`_owns_client`), the request
body's token budget (`max_tokens` / `max_completion_tokens`), and the answer
text extraction's block-type guard. All pinned at the provider seam with a
mock transport.
"""

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.services.screening.providers import (
    AnthropicProvider,
    OpenAICompatibleProvider,
    ProviderResponseError,
)

from .test_screening import _jpeg_bytes, _provider_settings


def test_anthropic_request_pins_token_budget_and_body_shape() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": '{"flagged": false}'}]},
        )

    async def call() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = AnthropicProvider(_provider_settings(), client=client)
        try:
            answer = await provider.complete(_jpeg_bytes(20, 20), "system")
            assert answer.text == '{"flagged": false}'
        finally:
            await client.aclose()

    asyncio.run(call())
    body = captured["body"]
    assert body["max_tokens"] == 1024
    assert body["system"] == "system"
    assert body["messages"][0]["role"] == "user"


def test_openai_request_pins_token_budget() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"flagged": false}'}}]},
        )

    async def call() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = OpenAICompatibleProvider(_provider_settings(), client=client)
        try:
            answer = await provider.complete(_jpeg_bytes(20, 20), "system")
            assert answer.text == '{"flagged": false}'
        finally:
            await client.aclose()

    asyncio.run(call())
    assert captured["body"]["max_completion_tokens"] == 1024


def test_anthropic_text_extraction_skips_non_text_and_non_dict_blocks() -> None:
    """A gateway answering junk alongside text blocks yields just the text:
    non-dict entries are skipped, not crashed on (the And in the guard is
    load-bearing: `or` would call .get() on a string)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "content": [
                    {"type": "text", "text": '{"flagged": '},
                    "not-a-dict-string",
                    {"type": "image", "source": {}},
                    {"type": "text", "text": "true}"},
                ]
            },
        )

    async def call() -> str:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = AnthropicProvider(_provider_settings(), client=client)
        try:
            answer = await provider.complete(_jpeg_bytes(20, 20), "system")
        finally:
            await client.aclose()
        return answer.text

    assert asyncio.run(call()) == '{"flagged": true}'


def test_injected_client_is_not_closed_by_provider_aclose() -> None:
    """`aclose()` closes only the provider-owned transport: a caller-injected
    client stays usable afterwards (tests and the worker share transports)."""

    def anthropic_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": [{"type": "text", "text": "{}"}]})

    def openai_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    async def call() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(anthropic_handler))
        provider = AnthropicProvider(_provider_settings(), client=client)
        await provider.aclose()
        # Still usable: an owned-and-closed client raises RuntimeError here.
        answer = await provider.complete(_jpeg_bytes(20, 20), "system")
        assert answer.text == "{}"
        await client.aclose()

        client = httpx.AsyncClient(transport=httpx.MockTransport(openai_handler))
        openai = OpenAICompatibleProvider(_provider_settings(), client=client)
        await openai.aclose()
        answer = await openai.complete(_jpeg_bytes(20, 20), "system")
        assert answer.text == "{}"
        await client.aclose()

    asyncio.run(call())


def test_owned_client_is_closed_by_provider_aclose() -> None:
    """Default construction owns the transport; aclose() really closes it."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": [{"type": "text", "text": "{}"}]})

    async def call() -> None:
        anthropic = AnthropicProvider(_provider_settings())
        await anthropic.aclose()
        with pytest.raises(RuntimeError):
            await anthropic.complete(_jpeg_bytes(20, 20), "system")

    asyncio.run(call())


def test_null_content_becomes_response_error_not_crash() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": None})

    async def call() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = AnthropicProvider(_provider_settings(), client=client)
        try:
            with pytest.raises(ProviderResponseError):
                await provider.complete(_jpeg_bytes(20, 20), "system")
        finally:
            await client.aclose()

    asyncio.run(call())
