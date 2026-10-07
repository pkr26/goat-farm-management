"""The advertised deletion budget covers the socket horizons actually configured."""

from typing import Any

from botocore.stub import ANY, Stubber

from app.services.screening import s3

from .test_screening import _cycle_settings


def test_advertised_purge_timeout_budget_covers_a_complete_native_sdk_path() -> None:
    storage = s3.ScreeningStorage(_cycle_settings())
    client = storage._ensure_delete_client()
    config = client.meta.config
    attempts = config.retries["total_max_attempts"]
    assert attempts == 1
    operations: list[str] = []

    def observed_request(params: dict[str, Any], model: Any, **kwargs: Any) -> None:
        operations.append(model.name)

    client.meta.events.register("before-parameter-build.s3", observed_request)
    bucket = storage.bucket
    key = "raw/1/versioned-purge.jpg"
    object_params = {"Bucket": bucket, "Key": key}
    list_params = {"Bucket": bucket, "Prefix": key, "MaxKeys": s3._PERMANENT_DELETE_PAGE_SIZE}
    versions = [{"Key": key, "VersionId": "retained-byte-version"}]
    markers = [{"Key": key, "VersionId": "current-delete-marker"}]
    with Stubber(client) as stubber:
        stubber.add_response("get_bucket_versioning", {"Status": "Enabled"}, {"Bucket": bucket})
        stubber.add_response("head_object", {"ContentLength": 17}, object_params)
        stubber.add_response("delete_object", {"DeleteMarker": True}, object_params)
        stubber.add_response(
            "list_object_versions",
            {"IsTruncated": False, "Versions": versions, "DeleteMarkers": markers},
            list_params,
        )
        stubber.add_response(
            "delete_objects",
            {},
            {"Bucket": bucket, "Delete": {"Objects": versions + markers, "Quiet": ANY}},
        )
        stubber.add_response(
            "list_object_versions",
            {"IsTruncated": False, "Versions": [], "DeleteMarkers": []},
            list_params,
        )
        stubber.add_client_error(
            "head_object",
            service_error_code="NoSuchKey",
            http_status_code=404,
            expected_params=object_params,
        )
        storage.delete_permanently([key])
        stubber.assert_no_pending_responses()
    assert {
        "GetBucketVersioning",
        "HeadObject",
        "DeleteObject",
        "ListObjectVersions",
        "DeleteObjects",
    }.issubset(operations)
    configured_path_horizon = (
        len(operations) * attempts * (config.connect_timeout + config.read_timeout)
    )
    assert 0 < configured_path_horizon < 40
    assert configured_path_horizon <= s3._S3_PERMANENT_DELETE_TIMEOUT_BUDGET_SECONDS
