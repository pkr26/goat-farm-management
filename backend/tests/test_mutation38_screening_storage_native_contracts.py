"""Real S3 adapters preserve snapshot, byte, deadline, and retention contracts."""

from __future__ import annotations

import base64
import io
import json
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from botocore.exceptions import BotoCoreError
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber
from pydantic import SecretStr

from app.core.config import ScreeningWorkerSettings
from app.services.screening import s3
from app.services.screening.s3 import (
    ScreeningObjectChangedError,
    ScreeningObjectMissingError,
    ScreeningObjectTooLargeError,
    ScreeningStorage,
    ScreeningStorageDeleteInProgress,
    ScreeningStorageError,
    storage_for_settings,
)


def _settings(**changes: Any) -> ScreeningWorkerSettings:
    values: dict[str, Any] = {
        "_env_file": None,
        "environment": "development",
        "screening_enabled": False,
        "s3_bucket": "native-screening-contracts",
        "s3_access_key_id": SecretStr("local-test-access"),
        "s3_secret_access_key": SecretStr("local-test-secret"),
    }
    return ScreeningWorkerSettings(**(values | changes))


def _succeeds[T](operation: Callable[[], T]) -> T:
    try:
        return operation()
    except (ScreeningStorageError, BotoCoreError, ValueError, TypeError, AttributeError) as exc:
        pytest.fail(f"A valid native object-storage operation must succeed: {exc}")


def test_real_clients_apply_the_finite_worker_socket_and_retry_policy() -> None:
    storage = ScreeningStorage(_settings())
    client = _succeeds(storage._ensure_client)
    delete_client = _succeeds(storage._ensure_delete_client)
    assert client is _succeeds(storage._ensure_client)
    assert delete_client is _succeeds(storage._ensure_delete_client)
    assert client is not delete_client
    assert client.meta.config.connect_timeout == client.meta.config.read_timeout == 5
    assert delete_client.meta.config.connect_timeout == delete_client.meta.config.read_timeout == 2
    assert (
        client.meta.config.retries
        == delete_client.meta.config.retries
        == {
            "total_max_attempts": 1,
            "mode": "standard",
        }
    )
    assert client.meta.config.signature_version == "s3v4"
    assert client.meta.config.s3 == {
        "addressing_style": "virtual",
        "us_east_1_regional_endpoint": "regional",
    }


@pytest.mark.parametrize("bucket", [None, "", "   "], ids=["missing", "empty", "blank"])
def test_disabled_runtime_configuration_still_fails_closed_at_native_storage(
    bucket: str | None,
) -> None:
    # Disabled worker settings legitimately omit S3 configuration. A direct
    # service caller must receive the declared storage error before transport.
    storage = ScreeningStorage(_settings(s3_bucket=bucket))
    for operation in (lambda: storage.bucket, storage._ensure_client):
        try:
            operation()
        except ScreeningStorageError:
            continue
        except (AttributeError, TypeError, BotoCoreError) as exc:
            pytest.fail(
                f"Missing storage configuration must fail through its typed interface: {exc}"
            )
        pytest.fail("An unconfigured storage service cannot admit an object-store operation")


def test_presigned_wire_policy_keeps_one_byte_admission_and_the_day_expiry_cap() -> None:
    storage = ScreeningStorage(_settings(screening_presign_expiry_seconds=86_400))
    signed = _succeeds(
        lambda: storage.presign_post(
            "raw/1/native.jpg", content_type="image/jpeg", upload_token="n" * 32, max_bytes=1
        )
    )
    policy = json.loads(base64.b64decode(signed.fields["policy"]))
    assert ["content-length-range", 1, 65_537] in policy["conditions"]
    assert {"x-amz-meta-screening-token": "n" * 32} in policy["conditions"]
    url = _succeeds(lambda: storage.presign_get("raw/1/native.jpg"))
    assert parse_qs(urlsplit(url).query)["X-Amz-Expires"] == ["86400"]


def test_head_preserves_real_response_snapshot_metadata() -> None:
    storage = ScreeningStorage(_settings())
    client = _succeeds(storage._ensure_client)
    with Stubber(client) as transport:
        transport.add_response(
            "head_object",
            {
                "ContentLength": 1,
                "ETag": '"native-etag"',
                "VersionId": "native-version",
                "ContentType": "image/jpeg",
                "Metadata": {"Screening-Token": "real-upload-token", "Farm-Id": "1"},
            },
            {"Bucket": "native-screening-contracts", "Key": "raw/1/native.jpg"},
        )
        info = _succeeds(lambda: storage.object_info("raw/1/native.jpg"))
        assert info is not None
        assert (info.size, info.etag, info.version_id, info.content_type) == (
            1,
            '"native-etag"',
            "native-version",
            "image/jpeg",
        )
        assert info.metadata == {"screening-token": "real-upload-token", "farm-id": "1"}
        transport.assert_no_pending_responses()


@pytest.mark.parametrize(
    ("etag", "version", "snapshot"),
    [
        (None, "native-version", {"VersionId": "native-version"}),
        ('"native-etag"', None, {"IfMatch": '"native-etag"'}),
    ],
    ids=["version", "etag"],
)
def test_one_byte_download_keeps_the_inspected_immutable_snapshot(
    etag: str | None, version: str | None, snapshot: dict[str, str]
) -> None:
    storage = ScreeningStorage(_settings())
    client = _succeeds(storage._ensure_client)
    stream = io.BytesIO(b"x")
    body = StreamingBody(stream, 1)
    with Stubber(client) as transport:
        transport.add_response(
            "get_object",
            {"ContentLength": 1, "Body": body},
            {"Bucket": "native-screening-contracts", "Key": "raw/1/native.jpg", **snapshot},
        )
        value = _succeeds(
            lambda: storage.download("raw/1/native.jpg", max_bytes=1, etag=etag, version_id=version)
        )
        assert value == b"x" and stream.closed
        transport.assert_no_pending_responses()


def test_zero_download_allowance_rejects_before_any_transport_request() -> None:
    storage = ScreeningStorage(_settings())
    client = _succeeds(storage._ensure_client)
    with Stubber(client) as transport:
        try:
            storage.download("raw/1/native.jpg", max_bytes=0)
        except ValueError as exc:
            assert str(exc) == "max_bytes must be positive"
        except ScreeningStorageError as exc:
            pytest.fail(f"A zero allowance must be rejected before transport: {exc}")
        else:
            pytest.fail("A download requires a positive byte allowance")
        transport.assert_no_pending_responses()


@pytest.mark.parametrize("elapsed", [19.5, 20.0], ids=["before-deadline", "at-deadline"])
def test_download_deadline_is_closed_at_twenty_seconds(
    monkeypatch: pytest.MonkeyPatch, elapsed: float
) -> None:
    # Only the external timer is controlled; the real client and stream remain
    # in place. No test waits for a stalled socket or treats a timeout as a kill.
    storage = ScreeningStorage(_settings())
    client = _succeeds(storage._ensure_client)
    stream = io.BytesIO(b"x")
    body = StreamingBody(stream, 1)
    ticks = iter([100.0, 100.0 + elapsed, 100.0 + elapsed])

    class TransferClock:
        @staticmethod
        def monotonic() -> float:
            return next(ticks)

    monkeypatch.setattr(s3, "time", TransferClock)
    with Stubber(client) as transport:
        transport.add_response(
            "get_object",
            {"ContentLength": 1, "Body": body},
            {"Bucket": "native-screening-contracts", "Key": "raw/1/native.jpg"},
        )
        if elapsed < 20:
            assert _succeeds(lambda: storage.download("raw/1/native.jpg", max_bytes=1)) == b"x"
        else:
            try:
                storage.download("raw/1/native.jpg", max_bytes=1)
            except ScreeningStorageError as exc:
                assert "20s transfer deadline" in str(exc)
            else:
                pytest.fail("The transfer cannot continue at its closed deadline")
        assert stream.closed
        transport.assert_no_pending_responses()


def test_incompatible_get_header_cannot_bypass_the_stream_memory_ceiling() -> None:
    storage = ScreeningStorage(_settings())
    client = _succeeds(storage._ensure_client)
    ceiling = 2 * 1024 * 1024
    stream = io.BytesIO(b"x" * (ceiling + 1024 * 1024))
    body = StreamingBody(stream, ceiling + 1024 * 1024)
    with Stubber(client) as transport:
        transport.add_response(
            "get_object",
            {"ContentLength": 1, "Body": body},
            {"Bucket": "native-screening-contracts", "Key": "raw/1/native.jpg"},
        )
        with pytest.raises(ScreeningObjectTooLargeError, match="while streaming"):
            storage.download("raw/1/native.jpg", max_bytes=ceiling)
        # This is botocore's real consumed-byte counter, not a fake body or a
        # copied loop. The adapter promises at most ceiling+one bytes in RAM.
        assert body._amount_read == ceiling + 1
        assert stream.closed
        transport.assert_no_pending_responses()


@pytest.mark.parametrize(
    ("code", "status", "expected"),
    [
        ("NoSuchKey", 404, ScreeningObjectMissingError),
        ("PreconditionFailed", 412, ScreeningObjectChangedError),
    ],
    ids=["upload-not-yet-present", "snapshot-replaced"],
)
def test_declared_download_retries_distinguish_missing_and_changed_snapshots(
    code: str, status: int, expected: type[ScreeningStorageError]
) -> None:
    storage = ScreeningStorage(_settings())
    client = _succeeds(storage._ensure_client)
    with Stubber(client) as transport:
        transport.add_client_error(
            "get_object",
            service_error_code=code,
            http_status_code=status,
            expected_params={"Bucket": "native-screening-contracts", "Key": "raw/1/native.jpg"},
        )
        try:
            storage.download("raw/1/native.jpg", max_bytes=1)
        except ScreeningStorageError as exc:
            assert type(exc) is expected
        else:
            pytest.fail("The provider refusal must retain its retry classification")
        transport.assert_no_pending_responses()


def test_head_does_not_misclassify_provider_denial_as_a_missing_upload() -> None:
    storage = ScreeningStorage(_settings())
    client = _succeeds(storage._ensure_client)
    with Stubber(client) as transport:
        transport.add_client_error(
            "head_object",
            service_error_code="AccessDenied",
            http_status_code=403,
            expected_params={"Bucket": "native-screening-contracts", "Key": "raw/1/native.jpg"},
        )
        with pytest.raises(ScreeningStorageError, match="S3 head failed"):
            storage.object_info("raw/1/native.jpg")
        transport.assert_no_pending_responses()


def test_versioned_delete_does_not_finalize_an_acknowledged_partial_failure() -> None:
    storage = ScreeningStorage(_settings())
    client = _succeeds(storage._ensure_delete_client)
    key = "raw/1/native.jpg"
    with Stubber(client) as transport:
        transport.add_response(
            "get_bucket_versioning", {"Status": "Enabled"}, {"Bucket": "native-screening-contracts"}
        )
        transport.add_client_error(
            "head_object",
            service_error_code="NoSuchKey",
            http_status_code=404,
            expected_params={"Bucket": "native-screening-contracts", "Key": key},
        )
        transport.add_response(
            "list_object_versions",
            {"Versions": [{"Key": key, "VersionId": "retained-version"}]},
            {"Bucket": "native-screening-contracts", "Prefix": key, "MaxKeys": 1000},
        )
        transport.add_response(
            "delete_objects",
            {"Errors": [{"Key": key, "VersionId": "retained-version", "Code": "AccessDenied"}]},
            {"Bucket": "native-screening-contracts", "Delete": ANY},
        )
        # If a false success discards the error, the actual retained version
        # remains visible. The saga must fail rather than claim bounded progress.
        transport.add_response(
            "list_object_versions",
            {"Versions": [{"Key": key, "VersionId": "retained-version"}]},
            {"Bucket": "native-screening-contracts", "Prefix": key, "MaxKeys": 1000},
        )
        try:
            storage.delete_permanently([key])
        except ScreeningStorageDeleteInProgress:
            pytest.fail("An explicit failed version deletion is an operational failure")
        except ScreeningStorageError as exc:
            assert "reported 1 object error" in str(exc)
        else:
            pytest.fail("An acknowledged deletion error cannot finalize raw-photo retention")


def test_real_settings_cache_preserves_its_thirty_two_instance_fifo_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A fresh cache isolates genuine settings instances; no object identity is
    # forged and every returned adapter is the production ScreeningStorage.
    monkeypatch.setattr(s3, "_STORAGE_BY_SETTINGS", {})
    settings = [_settings() for _ in range(33)]
    storages = [storage_for_settings(value) for value in settings[:32]]
    assert all(isinstance(value, ScreeningStorage) for value in storages)
    assert storage_for_settings(settings[0]) is storages[0]
    assert storage_for_settings(settings[31]) is storages[31]
    thirty_third = storage_for_settings(settings[32])
    assert isinstance(thirty_third, ScreeningStorage)
    assert storage_for_settings(settings[1]) is storages[1]
    assert storage_for_settings(settings[0]) is not storages[0]
