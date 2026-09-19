"""Thin sync boto3 wrapper for the screening bucket.

Every method is synchronous (boto3 has no native asyncio API) and the
pipeline wraps each call in ``asyncio.to_thread``. Constructing the client
is deferred to first use so importing this module never requires
screening credentials.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, cast

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from ...core.config import ScreeningRuntimeSettings

logger = logging.getLogger(__name__)

# Presigning is local SigV4 signing — no network round-trip — but boto3
# still requires the credentials; an over-long expiry buys nothing.
_PRESIGN_EXPIRY_CAP_SECONDS = 86_400
_DOWNLOAD_CHUNK_BYTES = 1024 * 1024
# The worker invokes this synchronous client through ``asyncio.to_thread``.
# Cancelling that coroutine does not stop a blocked thread, and asyncio waits
# for executor work at process shutdown. Keep each S3 socket operation and a
# complete capped download below Compose's finite worker stop grace instead
# of relying on botocore's ~60-second defaults/retry backoff.
_S3_CONNECT_TIMEOUT_SECONDS = 5
_S3_READ_TIMEOUT_SECONDS = 5
_S3_TOTAL_MAX_ATTEMPTS = 1
_S3_DOWNLOAD_DEADLINE_SECONDS = 20
# S3 evaluates a POST policy's content-length range against the multipart
# request, not just the object payload.  This bounded allowance covers the
# policy/signature fields, MIME boundaries and a maximal client filename so a
# file exactly at the advertised object cap remains uploadable.  The worker
# derives its own HEAD/stream ceiling from this same allowance, so an object
# the policy accepted is never rejected downstream (the two bounds must move
# together — see pipeline.MAX_DOWNLOAD_BYTES).
POST_MULTIPART_OVERHEAD_BYTES = 64 * 1024


class ScreeningStorageError(Exception):
    """Any S3 interaction failure; the pipeline records it on the image."""


class ScreeningObjectMissingError(ScreeningStorageError):
    """The object is not (yet) in the bucket. Upload flows pre-create PENDING
    image rows before the phone finishes its PUT, so the worker treats this
    as "come back next cycle", not an error."""


class ScreeningObjectChangedError(ScreeningStorageError):
    """An object changed after its metadata was inspected.

    The worker uses an ETag conditional GET (or an object version when the
    bucket has versioning) so a presigned uploader cannot swap bytes between
    the size/type check and the decode.  This is retryable: a later cycle
    will HEAD the new immutable snapshot.
    """


class ScreeningObjectTooLargeError(ScreeningStorageError):
    """A download exceeded the caller's hard byte ceiling."""


@dataclass(frozen=True)
class ScreeningObjectInfo:
    """The immutable facts checked before a raw image is downloaded."""

    size: int
    etag: str | None
    version_id: str | None
    content_type: str | None
    metadata: dict[str, str]


@dataclass(frozen=True)
class PresignedPost:
    """Browser form target plus the policy-bound fields S3 requires."""

    url: str
    fields: dict[str, str]


class ScreeningStorage:
    """List/download/upload/presign within one configured bucket."""

    def __init__(self, settings: ScreeningRuntimeSettings) -> None:
        self._settings = settings
        self._client = None

    @property
    def bucket(self) -> str:
        bucket = self._settings.s3_bucket
        if not bucket or not bucket.strip():
            # Settings normally reject this before storage is constructed,
            # but direct service callers must not get an AssertionError (or
            # an accidental ``None`` passed to boto3 under ``python -O``).
            raise ScreeningStorageError("screening S3 bucket is not configured")
        return bucket

    def _ensure_client(self) -> Any:
        if self._client is None:
            s = self._settings
            if (
                not s.s3_bucket
                or not s.s3_bucket.strip()
                or not s.s3_access_key_id
                or not s.s3_access_key_id.get_secret_value().strip()
                or not s.s3_secret_access_key
                or not s.s3_secret_access_key.get_secret_value().strip()
            ):
                raise ScreeningStorageError("screening S3 settings incomplete (bucket/credentials)")
            try:
                self._client = boto3.client(
                    "s3",
                    endpoint_url=s.s3_endpoint_url,
                    region_name=s.s3_region,
                    aws_access_key_id=s.s3_access_key_id.get_secret_value(),
                    aws_secret_access_key=s.s3_secret_access_key.get_secret_value(),
                    config=BotoConfig(
                        # Pipeline-level retry/backoff already handles a
                        # transient object-store failure. Retrying hidden
                        # inside one non-cancellable worker thread makes a
                        # SIGTERM routinely outlive its 40-second grace.
                        retries={"total_max_attempts": _S3_TOTAL_MAX_ATTEMPTS, "mode": "standard"},
                        connect_timeout=_S3_CONNECT_TIMEOUT_SECONDS,
                        read_timeout=_S3_READ_TIMEOUT_SECONDS,
                        signature_version="s3v4",
                        # Direct browser CSP needs to name the exact origin
                        # returned by a presigned POST/GET.  Keep AWS S3
                        # proper on virtual-hosted *regional* endpoints,
                        # including us-east-1, rather than silently yielding
                        # the legacy global s3.amazonaws.com host.  A custom
                        # S3-compatible endpoint retains its own addressing
                        # behavior/configuration.
                        s3=(
                            {
                                "addressing_style": "virtual",
                                "us_east_1_regional_endpoint": "regional",
                            }
                            if s.s3_endpoint_url is None
                            else None
                        ),
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

    def object_info(self, key: str) -> ScreeningObjectInfo | None:
        """HEAD metadata, or ``None`` while a presigned upload is absent.

        The returned ETag/version is passed back to ``download`` as a
        conditional snapshot selector.  That closes the otherwise classic
        HEAD→GET time-of-check/time-of-use window for a still-valid upload
        form.  Metadata is normalized to lower-case because S3 does the same
        for user metadata headers.
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
        metadata = {
            str(metadata_key).lower(): str(metadata_value)
            for metadata_key, metadata_value in cast(
                dict[str, object], response.get("Metadata") or {}
            ).items()
        }
        content_type = response.get("ContentType")
        return ScreeningObjectInfo(
            size=int(response["ContentLength"]),
            etag=cast(str | None, response.get("ETag")),
            version_id=cast(str | None, response.get("VersionId")),
            content_type=cast(str | None, content_type),
            metadata=metadata,
        )

    def object_size(self, key: str) -> int | None:
        """Compatibility helper for callers interested only in size."""
        info = self.object_info(key)
        return None if info is None else info.size

    def download(
        self,
        key: str,
        *,
        max_bytes: int,
        etag: str | None = None,
        version_id: str | None = None,
    ) -> bytes:
        """Read one immutable object snapshot without exceeding ``max_bytes``.

        A trusted ``ContentLength`` alone is not a memory limit: an object
        can be replaced after HEAD, and incompatible S3 implementations can
        report broken metadata.  We therefore reject an oversized GET header
        *and* stream-read at most ``max_bytes + 1`` bytes before joining.
        """
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        client = self._ensure_client()
        params: dict[str, object] = {"Bucket": self.bucket, "Key": key}
        if version_id is not None:
            params["VersionId"] = version_id
        elif etag is not None:
            params["IfMatch"] = etag
        try:
            deadline = time.monotonic() + _S3_DOWNLOAD_DEADLINE_SECONDS
            response = client.get_object(**params)
            content_length = int(response.get("ContentLength", 0))
            if content_length > max_bytes:
                raise ScreeningObjectTooLargeError(
                    f"object exceeds {max_bytes} byte download cap ({content_length} bytes)"
                )
            body = response["Body"]
            chunks: list[bytes] = []
            read = 0
            try:
                while True:
                    if time.monotonic() >= deadline:
                        raise ScreeningStorageError(
                            "S3 download exceeded "
                            f"{_S3_DOWNLOAD_DEADLINE_SECONDS}s transfer deadline"
                        )
                    chunk = cast(bytes, body.read(min(_DOWNLOAD_CHUNK_BYTES, max_bytes - read + 1)))
                    if not chunk:
                        break
                    read += len(chunk)
                    if read > max_bytes:
                        raise ScreeningObjectTooLargeError(
                            f"object exceeds {max_bytes} byte download cap while streaming"
                        )
                    chunks.append(chunk)
            finally:
                body.close()
            return b"".join(chunks)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"NoSuchKey", "404", "NotFound"}:
                raise ScreeningObjectMissingError(f"object not in bucket yet: {key!r}") from exc
            if code in {"PreconditionFailed", "412"}:
                raise ScreeningObjectChangedError(
                    f"object changed while being claimed: {key!r}"
                ) from exc
            raise ScreeningStorageError(f"S3 download failed for {key!r}: {exc}") from exc
        except BotoCoreError as exc:
            raise ScreeningStorageError(f"S3 download failed for {key!r}: {exc}") from exc

    def upload(self, key: str, data: bytes, content_type: str) -> None:
        client = self._ensure_client()
        try:
            client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        except (BotoCoreError, ClientError) as exc:
            raise ScreeningStorageError(f"S3 upload failed for {key!r}: {exc}") from exc

    def presign_post(
        self,
        key: str,
        *,
        content_type: str,
        upload_token: str,
        max_bytes: int,
    ) -> PresignedPost:
        """Mint a policy-bound browser upload form.

        Unlike a presigned PUT, a POST policy can enforce content-length
        range server-side.  The token is an S3 user-metadata condition bound
        to the pre-created database row; the worker refuses an object whose
        HEAD metadata does not match it.
        """
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        client = self._ensure_client()
        fields = {
            "Content-Type": content_type,
            "x-amz-meta-screening-token": upload_token,
            "success_action_status": "201",
        }
        try:
            response = client.generate_presigned_post(
                Bucket=self.bucket,
                Key=key,
                Fields=fields,
                Conditions=[
                    {"Content-Type": content_type},
                    {"x-amz-meta-screening-token": upload_token},
                    {"success_action_status": "201"},
                    ["content-length-range", 1, max_bytes + POST_MULTIPART_OVERHEAD_BYTES],
                ],
                ExpiresIn=min(
                    self._settings.screening_presign_expiry_seconds,
                    _PRESIGN_EXPIRY_CAP_SECONDS,
                ),
            )
        except (BotoCoreError, ClientError) as exc:
            raise ScreeningStorageError(f"S3 presign failed for {key!r}: {exc}") from exc
        returned_fields = cast(dict[str, object], response["fields"])
        return PresignedPost(
            url=cast(str, response["url"]),
            fields={field: str(value) for field, value in returned_fields.items()},
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
