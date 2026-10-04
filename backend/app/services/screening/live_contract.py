"""Optional live contract probe for screening storage and model providers.

This module is deliberately outside the ordinary test suite.  A scheduled or
manually dispatched workflow may opt in with sandbox credentials; pull-request
CI remains hermetic.  The probe sends only a generated solid-colour JPEG and
its artifact contains no object key, bucket name, credential, prompt, or raw
provider response.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from PIL import Image

from ...core.config import ScreeningWorkerSettings
from .providers import VisionProvider, build_provider_rotation, gate
from .s3 import ScreeningObjectInfo, ScreeningStorage

REPORT_SCHEMA_VERSION = 1
MAX_FIXTURE_BYTES = 8 * 1024


class LiveContractFailure(RuntimeError):
    """A sanitized failure suitable for logs and retained CI artifacts."""

    def __init__(self, stage: str, component: str | None = None) -> None:
        super().__init__(f"screening live contract failed at {stage}")
        self.stage = stage
        self.component = component


class StorageBoundary(Protocol):
    """Narrow seam used by the real S3 adapter and hermetic unit fakes."""

    def upload(self, key: str, data: bytes, content_type: str) -> None: ...

    def object_info(self, key: str) -> ScreeningObjectInfo | None: ...

    def download(
        self,
        key: str,
        *,
        max_bytes: int,
        etag: str | None = None,
        version_id: str | None = None,
    ) -> bytes: ...

    def delete_permanently(self, keys: Sequence[str]) -> None: ...


@runtime_checkable
class ClosableProvider(Protocol):
    async def aclose(self) -> None: ...


class ProviderTarget:
    """One real adapter plus the non-secret metadata retained in evidence."""

    def __init__(self, provider: VisionProvider, *, endpoint: str) -> None:
        self.provider = provider
        self.endpoint = endpoint


def generated_fixture() -> bytes:
    """Return a deterministic, non-sensitive JPEG small enough for a cheap call."""
    buffer = BytesIO()
    with Image.new("RGB", (64, 64), color=(128, 128, 128)) as image:
        image.save(buffer, format="JPEG", quality=70, optimize=True)
    fixture = buffer.getvalue()
    if not fixture or len(fixture) > MAX_FIXTURE_BYTES:
        raise LiveContractFailure("fixture_generation")
    return fixture


def _endpoint(base_url: str, suffix: str) -> str:
    return f"{base_url.rstrip('/')}/{suffix.lstrip('/')}"


def _safe_endpoint_metadata(endpoint: str) -> str:
    """Retain the network origin while dropping userinfo, path, and query data."""
    parsed = urlsplit(endpoint)
    host = parsed.hostname
    if not host:
        raise LiveContractFailure("endpoint_metadata")
    authority = f"[{host}]" if ":" in host else host
    if parsed.port is not None:
        authority = f"{authority}:{parsed.port}"
    return urlunsplit((parsed.scheme, authority, "", "", ""))


def configured_provider_targets(settings: ScreeningWorkerSettings) -> list[ProviderTarget]:
    """Build the production adapters and pair them with safe endpoint metadata."""
    providers = build_provider_rotation(settings)
    endpoints: list[str]
    if settings.screening_provider_rotation:
        endpoints = [
            _endpoint(
                entry.base_url
                or (
                    settings.screening_anthropic_base_url
                    if entry.kind == "anthropic"
                    else settings.screening_openai_base_url
                ),
                "v1/messages" if entry.kind == "anthropic" else "chat/completions",
            )
            for entry in settings.screening_provider_rotation
        ]
    elif settings.screening_provider == "anthropic":
        endpoints = [_endpoint(settings.screening_anthropic_base_url, "v1/messages")]
    else:
        endpoints = [_endpoint(settings.screening_openai_base_url, "chat/completions")]
    return [
        ProviderTarget(provider, endpoint=_safe_endpoint_metadata(endpoint))
        for provider, endpoint in zip(providers, endpoints, strict=True)
    ]


async def run_live_contract(
    *,
    storage: StorageBoundary,
    providers: Sequence[ProviderTarget],
    object_prefix: str,
    storage_endpoint: str,
    storage_region: str,
    fixture: bytes | None = None,
) -> dict[str, object]:
    """Cross the configured storage and provider boundaries once, then clean up.

    Every configured provider is checked so a stale secondary credential or
    changed response schema cannot hide behind a healthy primary.  Cleanup is
    attempted after any ambiguous upload failure and is itself fail-closed.
    """
    image = fixture if fixture is not None else generated_fixture()
    if not image or len(image) > MAX_FIXTURE_BYTES:
        raise LiveContractFailure("fixture_validation")
    key = f"{object_prefix.rstrip('/')}/contract-probe/{uuid4().hex}.jpg"
    provider_evidence: list[dict[str, object]] = []
    failure: LiveContractFailure | None = None
    upload_attempted = False

    try:
        upload_attempted = True
        try:
            storage.upload(key, image, "image/jpeg")
        except Exception:
            failure = LiveContractFailure("storage_upload")

        downloaded: bytes | None = None
        if failure is None:
            try:
                info = storage.object_info(key)
                if info is None or info.size != len(image) or info.content_type != "image/jpeg":
                    raise ValueError("stored fixture metadata mismatch")
                downloaded = storage.download(
                    key,
                    max_bytes=MAX_FIXTURE_BYTES,
                    etag=info.etag,
                    version_id=info.version_id,
                )
                if downloaded != image:
                    raise ValueError("stored fixture bytes mismatch")
            except Exception:
                failure = LiveContractFailure("storage_read")

        if failure is None and downloaded is not None:
            for target in providers:
                try:
                    result = await gate(target.provider, downloaded)
                    if (
                        result.provider != target.provider.name
                        or result.model != target.provider.model
                    ):
                        raise ValueError("provider identity metadata mismatch")
                    provider_evidence.append(
                        {
                            "name": result.provider,
                            "model": result.model,
                            "endpoint": target.endpoint,
                            "prompt_version": result.prompt_version,
                            "latency_ms": result.latency_ms,
                            "contract": {
                                "flagged": result.response.flagged,
                                "quality_problem": result.response.quality_problem,
                                "confidence": result.response.confidence,
                                "observation_count": len(result.response.observations),
                            },
                        }
                    )
                except Exception:
                    failure = LiveContractFailure("provider_contract", target.provider.name)
                    break
    finally:
        for target in providers:
            if isinstance(target.provider, ClosableProvider):
                try:
                    await target.provider.aclose()
                except Exception:
                    if failure is None:
                        failure = LiveContractFailure("provider_close", target.provider.name)
        if upload_attempted:
            try:
                storage.delete_permanently([key])
                if storage.object_info(key) is not None:
                    raise ValueError("fixture remained readable after permanent delete")
            except Exception:
                failure = LiveContractFailure("storage_cleanup")

    if failure is not None:
        raise failure from None
    if not provider_evidence:
        raise LiveContractFailure("provider_configuration")
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "passed",
        "fixture": {
            "kind": "generated-solid-jpeg",
            "bytes": len(image),
            "sha256": hashlib.sha256(image).hexdigest(),
        },
        "storage": {
            "adapter": "ScreeningStorage",
            "endpoint": storage_endpoint,
            "region": storage_region,
            "upload_read_match": True,
            "permanent_delete_verified": True,
        },
        "providers": provider_evidence,
    }


def _enabled(raw: str | None) -> bool:
    normalized = (raw or "").strip().lower()
    if normalized in {"", "0", "false", "no", "off"}:
        return False
    if normalized in {"1", "true", "yes", "on"}:
        return True
    raise ValueError("SCREENING_CONTRACT_ENABLED must be a boolean")


def _storage_endpoint(settings: ScreeningWorkerSettings) -> str:
    if settings.s3_endpoint_url:
        return _safe_endpoint_metadata(settings.s3_endpoint_url)
    return f"https://s3.{settings.s3_region}.amazonaws.com"


def _revision() -> str:
    candidate = os.environ.get("GITHUB_SHA", "")
    return candidate.lower() if re.fullmatch(r"[0-9a-fA-F]{40}", candidate) else "local"


def _base_report(status: str) -> dict[str, object]:
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": status,
        "checked_at": datetime.now(UTC).isoformat(),
        "revision": _revision(),
    }


async def execute_from_environment() -> tuple[dict[str, object], int]:
    """Resolve opt-in/config and return sanitized evidence plus an exit code."""
    try:
        enabled = _enabled(os.environ.get("SCREENING_CONTRACT_ENABLED"))
    except ValueError:
        return {**_base_report("failed"), "failed_stage": "opt_in_configuration"}, 2
    if not enabled:
        return {
            **_base_report("skipped"),
            "reason": "SCREENING_CONTRACT_ENABLED is not true; no external call was made",
        }, 0
    try:
        settings = ScreeningWorkerSettings(screening_enabled=True)
        targets = configured_provider_targets(settings)
    except Exception:
        return {**_base_report("failed"), "failed_stage": "configuration"}, 2
    try:
        evidence = await run_live_contract(
            storage=ScreeningStorage(settings),
            providers=targets,
            object_prefix=settings.screening_s3_prefix,
            storage_endpoint=_storage_endpoint(settings),
            storage_region=settings.s3_region,
        )
    except LiveContractFailure as exc:
        report = {**_base_report("failed"), "failed_stage": exc.stage}
        if exc.component is not None:
            report["failed_component"] = exc.component
        return report, 1
    return {**_base_report("passed"), **evidence}, 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report, exit_code = asyncio.run(execute_from_environment())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"screening live contract: {report['status']}")
    if report["status"] == "failed":
        print(f"failed stage: {report['failed_stage']}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
