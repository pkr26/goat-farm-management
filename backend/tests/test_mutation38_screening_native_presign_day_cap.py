"""The declared native runtime keeps signed links within the storage day cap."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import SecretStr

from app.core.config import ScreeningRuntimeSettings, ScreeningWorkerSettings
from app.services.screening.s3 import ScreeningStorage


class NativeScreeningRuntime(ScreeningRuntimeSettings):
    """A complete typed runtime projection, independent of API env validators."""

    def __init__(self) -> None:
        values: dict[str, Any] = {
            "_env_file": None,
            "environment": "development",
            "screening_enabled": False,
            "s3_bucket": "native-presign-cap",
            "s3_access_key_id": SecretStr("local-test-access"),
            "s3_secret_access_key": SecretStr("local-test-secret"),
        }
        seed = ScreeningWorkerSettings(**values)
        self.environment = seed.environment
        self.database_url = seed.database_url
        self.db_sslmode = seed.db_sslmode
        self.db_sslrootcert_path = seed.db_sslrootcert_path
        self.db_pool_size = seed.db_pool_size
        self.db_max_overflow = seed.db_max_overflow
        self.db_pool_timeout = seed.db_pool_timeout
        self.db_statement_timeout_ms = seed.db_statement_timeout_ms
        self.screening_enabled = seed.screening_enabled
        self.s3_endpoint_url = seed.s3_endpoint_url
        self.s3_region = seed.s3_region
        self.s3_bucket = seed.s3_bucket
        self.s3_access_key_id = seed.s3_access_key_id
        self.s3_secret_access_key = seed.s3_secret_access_key
        self.screening_s3_prefix = seed.screening_s3_prefix
        self.screening_poll_interval_seconds = seed.screening_poll_interval_seconds
        self.screening_max_images_per_cycle = seed.screening_max_images_per_cycle
        self.screening_daily_call_budget_per_farm = seed.screening_daily_call_budget_per_farm
        self.screening_image_max_edge_px = seed.screening_image_max_edge_px
        self.screening_crop_detection_enabled = seed.screening_crop_detection_enabled
        self.screening_max_crops_per_image = seed.screening_max_crops_per_image
        self.screening_presign_expiry_seconds = 86_401
        self.screening_provider = seed.screening_provider
        self.screening_anthropic_base_url = seed.screening_anthropic_base_url
        self.screening_anthropic_api_key = seed.screening_anthropic_api_key
        self.screening_anthropic_model = seed.screening_anthropic_model
        self.screening_openai_base_url = seed.screening_openai_base_url
        self.screening_openai_api_key = seed.screening_openai_api_key
        self.screening_openai_model = seed.screening_openai_model
        self.screening_provider_rotation = seed.screening_provider_rotation
        self.screening_provider_timeout_seconds = seed.screening_provider_timeout_seconds
        self.screening_stale_processing_after_seconds = (
            seed.screening_stale_processing_after_seconds
        )


@pytest.mark.parametrize("operation", ["get", "post"], ids=["get", "post"])
def test_native_runtime_overlong_expiry_obeys_the_signed_day_cap(
    operation: str,
) -> None:
    # The adapter advertises a local cap even for a structural runtime that
    # supplies a longer TTL. Keep real SigV4 signing; inspect the actual wire
    # expiry rather than the implementation constant or a mocked signer.
    storage = ScreeningStorage(NativeScreeningRuntime())
    if operation == "get":
        signed = storage.presign_get("raw/1/native.jpg")
        assert parse_qs(urlsplit(signed).query)["X-Amz-Expires"] == ["86400"]
    else:
        signed_post = storage.presign_post(
            "raw/1/native.jpg",
            content_type="image/jpeg",
            upload_token="n" * 32,
            max_bytes=1,
        )
        policy = json.loads(base64.b64decode(signed_post.fields["policy"]))
        stamp = signed_post.fields["x-amz-date"]
        issued = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        expires = datetime.fromisoformat(policy["expiration"].replace("Z", "+00:00"))
        assert (expires - issued).total_seconds() == 86_400
