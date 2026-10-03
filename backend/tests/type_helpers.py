"""Typed boundaries for dynamic HTTP JSON fixtures and request headers.

JSON response values are intentionally dynamic; these helpers verify their
outer shape before returning the declared fixture type. Domain models retain
their own precise schemas and are not converted to Any.
"""

from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import QueuePool

type Headers = dict[str, str]
type JsonObject = dict[str, Any]


def json_object(value: object) -> JsonObject:
    assert isinstance(value, dict), f"Expected JSON object, got {type(value).__name__}"
    assert all(isinstance(key, str) for key in value), "JSON object keys must be strings"
    return cast(JsonObject, value)


def json_objects(value: object) -> list[JsonObject]:
    assert isinstance(value, list), f"Expected JSON array, got {type(value).__name__}"
    return [json_object(item) for item in value]


def json_int(value: object) -> int:
    assert isinstance(value, int) and not isinstance(value, bool), "Expected JSON integer"
    return value


def json_string(value: object) -> str:
    assert isinstance(value, str), "Expected JSON string"
    return value


def checked_out_connections(engine: AsyncEngine) -> int:
    pool = engine.sync_engine.pool
    assert isinstance(pool, QueuePool), "Connection-count assertions require a queue pool"
    return pool.checkedout()
