"""Thin sync boto3 wrapper for the screening bucket.

Every method is synchronous (boto3 has no native asyncio API) and the
pipeline wraps each call in ``asyncio.to_thread``. Constructing the client
is deferred to first use so importing this module never requires
screening credentials.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, cast

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from ...core.config import Settings

logger = logging.getLogger(__name__)

# Presigning is local SigV4 signing — no network round-trip — but boto3
# still requires the credentials; an over-long expiry buys nothing.
_PRESIGN_EXPIRY_CAP_SECONDS = 86_400


class ScreeningStorageError(Exception):
    """Any S3 interaction failure; the pipeline records it on the image."""


class ScreeningObjectMissingError(ScreeningStorageError):
    """The object is not (yet) in the bucket. Upload flows pre-create PENDING
    image rows before the phone finishes its PUT, so the worker treats this
    as "come back next cycle", not an error."""


class ScreeningStorage:
    """List/download/upload/presign within one configured bucket."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = None

    @property
    def bucket(self) -> str:
        assert self._settings.s3_bucket is not None  # validated when enabled
        return self._settings.s3_bucket

    def _ensure_client(self) -> Any:
        if self._client is None:
            s = self._settings
            if not s.s3_bucket or not s.s3_access_key_id or not s.s3_secret_access_key:
                raise ScreeningStorageError("screening S3 settings incomplete (bucket/credentials)")
            try:
                self._client = boto3.client(
                    "s3",
                    endpoint_url=s.s3_endpoint_url,
                    region_name=s.s3_region,
                    aws_access_key_id=s.s3_access_key_id.get_secret_value(),
                    aws_secret_access_key=s.s3_secret_access_key.get_secret_value(),
                    config=BotoConfig(
                        # Long listings and 20 MB photo downloads are the
                        # norm; the default retry budget suits quick metadata
                        # calls, not transfers.
                        retries={"max_attempts": 3, "mode": "adaptive"},
                        signature_version="s3v4",
                    ),
                )
            except (BotoCoreError, ClientError) as exc:
                raise ScreeningStorageError(f"S3 client construction failed: {exc}") from exc
        return self._client

    def list_object_keys(self, prefix: str, max_keys: int) -> list[str]:
        """Up to ``max_keys`` object keys under ``prefix`` (lexicographic)."""
        client = self._ensure_client()
        collected: list[str] = []
        try:
            paginator = client.get_paginator("list_objects_v2")
            for page in paginator.paginate(
                Bucket=self.bucket, Prefix=prefix, PaginationConfig={"MaxItems": max_keys}
            ):
                for item in page.get("Contents", []):
                    collected.append(item["Key"])
                if len(collected) >= max_keys:
                    break
        except (BotoCoreError, ClientError) as exc:
            raise ScreeningStorageError(f"S3 list failed under {prefix!r}: {exc}") from exc
        return collected[:max_keys]

    def object_size(self, key: str) -> int | None:
        """ContentLength via HEAD, or None when the object is missing.

        A metadata read sizes a download before any byte is transferred,
        so the pipeline can refuse oversized objects without reading
        them; other HEAD failures are storage errors like any other.
        """
        client = self._ensure_client()
        try:
            response = client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"NoSuchKey", "404", "NotFound"}:
                return None
            raise ScreeningStorageError(f"S3 head failed for {key!r}: {exc}") from exc
        except BotoCoreError as exc:
            raise ScreeningStorageError(f"S3 head failed for {key!r}: {exc}") from exc
        return int(response["ContentLength"])

    def download(self, key: str) -> bytes:
        client = self._ensure_client()
        try:
            response = client.get_object(Bucket=self.bucket, Key=key)
            return cast(bytes, response["Body"].read())
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"NoSuchKey", "404", "NotFound"}:
                raise ScreeningObjectMissingError(f"object not in bucket yet: {key!r}") from exc
            raise ScreeningStorageError(f"S3 download failed for {key!r}: {exc}") from exc
        except BotoCoreError as exc:
            raise ScreeningStorageError(f"S3 download failed for {key!r}: {exc}") from exc

    def upload(self, key: str, data: bytes, content_type: str) -> None:
        client = self._ensure_client()
        try:
            client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        except (BotoCoreError, ClientError) as exc:
            raise ScreeningStorageError(f"S3 upload failed for {key!r}: {exc}") from exc

    def presign_put(self, key: str, content_type: str) -> str:
        """Short-lived direct-upload URL. The signed content-type header is
        part of the signature, so the client must send exactly this value
        on its PUT."""
        client = self._ensure_client()
        return cast(
            str,
            client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self.bucket,
                    "Key": key,
                    "ContentType": content_type,
                },
                ExpiresIn=min(
                    self._settings.screening_presign_expiry_seconds,
                    _PRESIGN_EXPIRY_CAP_SECONDS,
                ),
            ),
        )

    def presign_get(self, key: str) -> str:
        client = self._ensure_client()
        return cast(
            str,
            client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=min(
                    self._settings.screening_presign_expiry_seconds, _PRESIGN_EXPIRY_CAP_SECONDS
                ),
            ),
        )


@lru_cache
def get_screening_storage() -> ScreeningStorage:
    """Process-wide storage instance (the worker and each API replica)."""
    from ...core.config import get_settings

    return ScreeningStorage(get_settings())
