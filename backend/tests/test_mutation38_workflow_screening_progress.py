"""Screening storage completes at EOF and respects its per-call purge budget."""

from __future__ import annotations

from typing import cast

import pytest
from botocore.exceptions import ClientError

from app.services.screening.s3 import (
    ScreeningStorage,
    ScreeningStorageDeleteInProgress,
    ScreeningStorageError,
)

from .test_screening import _cycle_settings


def test_supported_download_finishes_on_the_first_eof() -> None:
    payload = b"complete-screening-object"

    class Body:
        calls = 0
        closed = False

        def read(self, amount: int) -> bytes:
            self.calls += 1
            assert self.calls <= 2, "A completed object must not be read again after EOF"
            assert amount > 0
            return payload if self.calls == 1 else b""

        def close(self) -> None:
            self.closed = True

    body = Body()

    class Client:
        def get_object(self, **_params: object) -> dict[str, object]:
            return {"ContentLength": len(payload), "Body": body}

    storage = ScreeningStorage(_cycle_settings())
    storage._client = Client()  # type: ignore[assignment]
    try:
        downloaded = storage.download("raw/1/complete.jpg", max_bytes=100)
    except ScreeningStorageError as exc:
        pytest.fail(f"A complete object within the download allowance must succeed: {exc}")
    assert downloaded == payload
    assert body.calls == 2 and body.closed


@pytest.mark.parametrize("versioning", ["Enabled", "Suspended"])
def test_versioned_purge_of_an_already_absent_key_completes(
    versioning: str,
) -> None:
    class Client:
        lists = 0
        heads = 0

        def get_bucket_versioning(self, **_params: object) -> dict[str, str]:
            return {"Status": versioning}

        def head_object(self, **_params: object) -> dict[str, object]:
            self.heads += 1
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "HeadObject")

        def list_object_versions(self, **_params: object) -> dict[str, object]:
            self.lists += 1
            assert self.lists <= 2, "Verified absence must not cause another purge scan"
            return {"Versions": [], "DeleteMarkers": [], "IsTruncated": False}

        def delete_object(self, **_params: object) -> None:
            pytest.fail("An absent key must not acquire a fresh delete marker")

        def delete_objects(self, **_params: object) -> dict[str, object]:
            pytest.fail("An empty version listing has nothing to delete")

    client = Client()
    storage = ScreeningStorage(_cycle_settings())
    storage._delete_client = client  # type: ignore[assignment]
    try:
        storage.delete_permanently(["raw/1/already-absent.jpg"])
    except ScreeningStorageError as exc:
        pytest.fail(f"An already absent versioned object must finish its purge: {exc}")
    assert client.lists == 2 and client.heads == 2


def test_versioned_purge_yields_after_one_acknowledged_page_if_versions_remain() -> None:
    key = "raw/1/not-yet-absent.jpg"

    class Client:
        lists = 0
        deleted: list[list[dict[str, str]]]

        def __init__(self) -> None:
            self.deleted = []

        def get_bucket_versioning(self, **_params: object) -> dict[str, str]:
            return {"Status": "Enabled"}

        def head_object(self, **_params: object) -> dict[str, object]:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "HeadObject")

        def list_object_versions(self, **_params: object) -> dict[str, object]:
            self.lists += 1
            assert self.lists <= 2, "One purge attempt must release its lease after one page"
            return {"Versions": [{"Key": key, "VersionId": "retained"}], "DeleteMarkers": []}

        def delete_object(self, **_params: object) -> None:
            pytest.fail("An absent current object must not acquire a delete marker")

        def delete_objects(self, **params: object) -> dict[str, object]:
            objects = cast(dict[str, object], params["Delete"])["Objects"]
            self.deleted.append(cast(list[dict[str, str]], objects))
            assert len(self.deleted) <= 1, "The per-call purge allowance is one version page"
            # An acknowledgment cannot prove absence; a still-visible version
            # must produce bounded progress and a later durable continuation.
            return {}

    client = Client()
    storage = ScreeningStorage(_cycle_settings())
    storage._delete_client = client  # type: ignore[assignment]
    with pytest.raises(ScreeningStorageDeleteInProgress, match="bounded progress"):
        storage.delete_permanently([key])
    assert client.lists == 2
    assert client.deleted == [[{"Key": key, "VersionId": "retained"}]]
