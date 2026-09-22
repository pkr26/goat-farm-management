"""Vision provider adapters.

One protocol (``VisionProvider``), one implementation per vendor wire
format. Adapters translate transport only — prompts and response contracts
live in ``gate``/``specialists`` so every provider answers the identical
question, which is what makes the Phase 2 rotation a fair comparison.

``complete()`` is the raw primitive (image + system prompt → answer text);
``gate()`` and the specialist calls are thin compositions over it.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Protocol

import httpx
from pydantic import SecretStr

from ...core.config import ScreeningRotationProvider, ScreeningRuntimeSettings
from .gate import GateResponse, gate_instruction, parse_gate_response


class ProviderError(Exception):
    """Transport- or contract-level failure; the pipeline records it and
    moves on. The image row is retried by a later cycle."""


async def _post_with_one_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    json: dict[str, object],
) -> httpx.Response:
    """POST with exactly one retry on a transient failure.

    ITEM 6 (2026-09-21 playbook): a 5xx blip or a timeout at the gateway
    edge otherwise costs a full image attempt (and re-pays the whole call
    on the retry cycle). One immediate retry catches the blip; anything
    persistent still fails fast for the rotation chain.
    """
    try:
        response = await client.post(url, headers=headers, json=json)
        response.raise_for_status()
        return response
    except (httpx.TimeoutException, httpx.TransportError):
        # Transient transport failure (timeout, reset, DNS blip): one
        # immediate retry before the whole image attempt is written off.
        try:
            retried = await client.post(url, headers=headers, json=json)
            retried.raise_for_status()
            return retried
        except httpx.HTTPError as exc:
            raise ProviderError(f"gate call failed: {exc}") from exc
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code < 500:
            raise
        retried = await client.post(url, headers=headers, json=json)
        retried.raise_for_status()
        return retried


class ProviderResponseError(ProviderError):
    """The endpoint answered but the payload was not usable."""


@dataclass(frozen=True)
class ProviderAnswer:
    """One completed model call, contract parsing not included."""

    text: str
    provider: str
    model: str
    latency_ms: int


@dataclass(frozen=True)
class GateCallResult:
    response: GateResponse
    provider: str
    model: str
    prompt_version: str
    latency_ms: int
    raw_text: str


class VisionProvider(Protocol):
    name: str
    model: str

    async def complete(self, image_jpeg: bytes, system_prompt: str) -> ProviderAnswer: ...


class AnthropicProvider:
    """Claude via the Messages API."""

    def __init__(
        self,
        settings: ScreeningRuntimeSettings,
        *,
        name: str = "anthropic",
        client: httpx.AsyncClient | None = None,
        rotation_entry: ScreeningRotationProvider | None = None,
    ) -> None:
        if rotation_entry is not None:
            key: SecretStr = rotation_entry.api_key
            self.model = rotation_entry.model
            base_url = rotation_entry.base_url or settings.screening_anthropic_base_url
        else:
            configured = settings.screening_anthropic_api_key
            if configured is None:
                raise ProviderError("screening_anthropic_api_key is not configured")
            key = configured
            self.model = settings.screening_anthropic_model
            base_url = settings.screening_anthropic_base_url
        self.name = name
        self._api_key = key.get_secret_value()
        self._base_url = base_url
        self._timeout = settings.screening_provider_timeout_seconds
        self._client = client or httpx.AsyncClient(timeout=self._timeout)
        # aclose() below must never close a caller-owned client (tests share
        # one transport across providers).
        self._owns_client = client is None

    async def aclose(self) -> None:
        """Close the owned httpx transport (worker shutdown; B-small fix)."""
        if self._owns_client:
            await self._client.aclose()

    async def complete(self, image_jpeg: bytes, system_prompt: str) -> ProviderAnswer:
        body = {
            "model": self.model,
            "max_tokens": 1024,
            "system": system_prompt,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": base64.b64encode(image_jpeg).decode("ascii"),
                            },
                        },
                        {"type": "text", "text": "Analyze this photo. JSON only."},
                    ],
                }
            ],
        }
        started = time.monotonic()
        try:
            answer = await _post_with_one_retry(
                self._client,
                f"{self._base_url}/v1/messages",
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                },
                json=body,
            )
            payload = answer.json()
            text = "".join(
                block.get("text", "")
                for block in payload.get("content", [])
                if isinstance(block, dict) and block.get("type") == "text"
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name} gate call failed: {exc}") from exc
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            # Same tuple as the OpenAI-compatible adapter: a gateway that
            # answers {"content": null} or a non-list must become a
            # ProviderResponseError for the fallback chain, not a raw
            # TypeError that crashes the caller.
            raise ProviderResponseError(
                f"{self.name} returned an unexpected payload shape: {exc}"
            ) from exc
        return ProviderAnswer(
            text=text,
            provider=self.name,
            model=self.model,
            latency_ms=int((time.monotonic() - started) * 1000),
        )


class OpenAICompatibleProvider:
    """Every OpenAI-shaped chat/completions endpoint (GLM, GPT, local
    gateways). Rotation across these is the Phase 2 norm; the transport is
    shared."""

    def __init__(
        self,
        settings: ScreeningRuntimeSettings,
        *,
        name: str = "openai_compatible",
        client: httpx.AsyncClient | None = None,
        rotation_entry: ScreeningRotationProvider | None = None,
    ) -> None:
        if rotation_entry is not None:
            key: SecretStr = rotation_entry.api_key
            self.model = rotation_entry.model
            base_url = rotation_entry.base_url or settings.screening_openai_base_url
        else:
            configured = settings.screening_openai_api_key
            if configured is None:
                raise ProviderError("screening_openai_api_key is not configured")
            key = configured
            self.model = settings.screening_openai_model
            base_url = settings.screening_openai_base_url
        self.name = name
        self._api_key = key.get_secret_value()
        self._base_url = base_url
        self._timeout = settings.screening_provider_timeout_seconds
        self._client = client or httpx.AsyncClient(timeout=self._timeout)
        self._owns_client = client is None

    async def aclose(self) -> None:
        """Close the owned httpx transport (worker shutdown; B-small fix)."""
        if self._owns_client:
            await self._client.aclose()

    async def complete(self, image_jpeg: bytes, system_prompt: str) -> ProviderAnswer:
        data_url = "data:image/jpeg;base64," + base64.b64encode(image_jpeg).decode("ascii")
        body = {
            "model": self.model,
            "max_completion_tokens": 1024,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Analyze this photo. JSON only."},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            # The JSON contract is judged after parsing; provider-level
            # enforcement is a bonus some endpoints reject.
            "response_format": {"type": "json_object"},
        }
        started = time.monotonic()
        try:
            answer = await _post_with_one_retry(
                self._client,
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=body,
            )
            payload = answer.json()
            text = payload["choices"][0]["message"]["content"]
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name} gate call failed: {exc}") from exc
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderResponseError(
                f"{self.name} returned an unexpected payload shape: {exc}"
            ) from exc
        return ProviderAnswer(
            text=text,
            provider=self.name,
            model=self.model,
            latency_ms=int((time.monotonic() - started) * 1000),
        )


async def gate(provider: VisionProvider, image_jpeg: bytes) -> GateCallResult:
    """Run the shared gate prompt through any provider."""
    instruction = gate_instruction()
    answer = await provider.complete(image_jpeg, instruction.system_prompt)
    try:
        response = parse_gate_response(answer.text)
    except Exception as exc:
        raise ProviderResponseError(
            f"{provider.name} answer failed the gate contract: {exc}"
        ) from exc
    return GateCallResult(
        response=response,
        provider=answer.provider,
        model=answer.model,
        prompt_version=instruction.prompt_version,
        latency_ms=answer.latency_ms,
        raw_text=answer.text[:2000],
    )


def build_provider_rotation(settings: ScreeningRuntimeSettings) -> list[VisionProvider]:
    """The configured provider list, rotation order preserved.

    Non-empty GOATFARM_SCREENING_PROVIDER_ROTATION wins; otherwise the
    Phase 1 single-provider fields build a one-entry list.
    """
    if settings.screening_provider_rotation:
        return [
            (
                AnthropicProvider(settings, name=entry.name, rotation_entry=entry, client=None)
                if entry.kind == "anthropic"
                else OpenAICompatibleProvider(
                    settings, name=entry.name, rotation_entry=entry, client=None
                )
            )
            for entry in settings.screening_provider_rotation
        ]
    if settings.screening_provider == "anthropic":
        return [AnthropicProvider(settings)]
    return [OpenAICompatibleProvider(settings)]
