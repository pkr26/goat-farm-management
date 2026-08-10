"""PostgreSQL-backed, transaction-bound HTTP idempotency.

Successful records are retained for ``GOATFARM_IDEMPOTENCY_RETENTION_HOURS``
(seven days by default). A scheduled, fixed-batch ``SKIP LOCKED`` purge bounds
retention without making a tenant request delete a global expiry cohort.
Failed/4xx/5xx mutations roll back their claim and are never replayed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..models import IdempotencyRecord
from ..models.idempotency import CREATE_FARM_IDEMPOTENCY_OPERATION
from ..utils import utcnow

MAX_IDEMPOTENCY_KEY_LENGTH = 128
SENSITIVE_IDEMPOTENCY_OPERATIONS = frozenset({"team.workers.create"})

# Keep keys opaque but bounded and safe for HTTP/logging infrastructure:
# printable, non-whitespace ASCII. The raw value is hashed before persistence.
_IdempotencyHeader = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=MAX_IDEMPOTENCY_KEY_LENGTH,
        pattern=r"^[\x21-\x7e]+$",
    ),
]


def single_idempotency_key(
    request: Request,
    value: _IdempotencyHeader = None,
) -> str | None:
    """Return one unambiguous retry identity.

    ASGI servers and reverse proxies need not choose the same member from
    duplicate request headers. Silently selecting one therefore lets the edge
    and application disagree about which mutation is being deduplicated.
    """
    values = request.headers.getlist("idempotency-key")
    if len(values) > 1:
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key must be supplied exactly once",
        )
    return value


IdempotencyKey = Annotated[str | None, Depends(single_idempotency_key)]


async def purge_expired_idempotency_records(
    db: AsyncSession,
    *,
    batch_size: int,
) -> int:
    """Delete at most ``batch_size`` expired rows without cleanup contention."""
    if not 1 <= batch_size <= 10_000:
        raise ValueError("batch_size must be between 1 and 10000")
    now = utcnow()
    candidates = (
        select(IdempotencyRecord.id)
        .where(IdempotencyRecord.expires_at <= now)
        .order_by(IdempotencyRecord.expires_at, IdempotencyRecord.id)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    # Materialize the bounded, locked candidate set before issuing DELETE.
    # PostgreSQL may otherwise re-evaluate a LIMIT subquery while rows are
    # being changed and delete more than ``batch_size`` in one call.
    candidate_ids = list((await db.execute(candidates)).scalars())
    if not candidate_ids:
        return 0
    removed = await db.execute(
        delete(IdempotencyRecord)
        .where(IdempotencyRecord.id.in_(candidate_ids))
        .returning(IdempotencyRecord.id)
    )
    return len(removed.scalars().all())


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_request(
    operation: str,
    payload: BaseModel,
    path_identity: Mapping[str, object],
) -> bytes:
    """Serialize the validated/default-expanded body and path deterministically."""
    return json.dumps(
        {
            "version": 1,
            "operation": operation,
            "path": dict(path_identity),
            "body": payload.model_dump(mode="json", exclude_none=False),
        },
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _request_hashes(
    operation: str,
    payload: BaseModel,
    path_identity: Mapping[str, object],
) -> tuple[str, tuple[str, ...]]:
    """Return the persisted fingerprint and every accepted replay fingerprint.

    Ordinary operations intentionally retain their historical SHA-256
    behavior. Operations whose bodies contain password material use a keyed
    fingerprint so a database/backup reader cannot test password guesses
    offline. Previous keys are verification-only during bounded rotations.
    """
    canonical = _canonical_request(operation, payload, path_identity)
    if operation not in SENSITIVE_IDEMPOTENCY_OPERATIONS:
        request_hash = _sha256(canonical)
        return request_hash, (request_hash,)

    settings = get_settings()
    secrets = (
        settings.idempotency_request_hmac_secret,
        *settings.idempotency_request_hmac_previous_secrets,
    )
    candidates = tuple(
        hmac.new(
            secret.get_secret_value().encode("utf-8"),
            canonical,
            hashlib.sha256,
        ).hexdigest()
        for secret in secrets
    )
    return candidates[0], candidates


def _request_hash_matches(
    operation: str,
    stored_hash: str,
    candidates: tuple[str, ...],
) -> bool:
    if operation not in SENSITIVE_IDEMPOTENCY_OPERATIONS:
        return stored_hash == candidates[0]

    # Evaluate every configured candidate; do not leak which rotation key
    # matched via short-circuit timing.
    matched = 0
    for candidate in candidates:
        matched |= int(hmac.compare_digest(stored_hash, candidate))
    return bool(matched)


async def replay_idempotent_if_committed[ResponseT: BaseModel](
    db: AsyncSession,
    *,
    http_response: Response,
    key: str,
    farm_id: int | None,
    actor_id: int,
    operation: str,
    payload: BaseModel,
    path_identity: Mapping[str, object],
    response_type: type[ResponseT],
) -> ResponseT | None:
    """Read an already-committed result without creating a claim.

    This is a read-only fast path for workflows that must release their
    authorization transaction before expensive off-thread preparation. A
    miss is only a hint: the caller must still use ``execute_idempotent``
    after preparation because another process can commit the same key in the
    meantime. Transaction cleanup deliberately remains the caller's job so it
    can combine this lookup with other cheap read-only preflight checks.
    """
    actor_scoped = farm_id is None
    if actor_scoped != (operation == CREATE_FARM_IDEMPOTENCY_OPERATION):
        raise ValueError(
            "farm_id may be omitted only for the actor-scoped "
            f"{CREATE_FARM_IDEMPOTENCY_OPERATION!r} operation"
        )
    if not 1 <= len(key) <= MAX_IDEMPOTENCY_KEY_LENGTH or any(
        not 0x21 <= ord(char) <= 0x7E for char in key
    ):
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key must be 1-128 printable non-whitespace ASCII characters",
        )

    now = utcnow()
    _current_hash, accepted_request_hashes = _request_hashes(
        operation,
        payload,
        path_identity,
    )
    key_digest = _sha256(key.encode("ascii"))
    existing = (
        await db.execute(
            select(IdempotencyRecord).where(
                IdempotencyRecord.farm_id == farm_id,
                IdempotencyRecord.actor_id == actor_id,
                IdempotencyRecord.operation == operation,
                IdempotencyRecord.key_digest == key_digest,
            )
        )
    ).scalar_one_or_none()
    if existing is None or existing.expires_at <= now:
        return None
    if not _request_hash_matches(operation, existing.request_hash, accepted_request_hashes):
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was already used with a different request",
        )
    if (
        existing.response_body is None
        or existing.response_status is None
        or existing.completed_at is None
    ):
        raise HTTPException(
            status_code=409,
            detail="Idempotency result is incomplete; retry with a new key",
        )
    http_response.status_code = existing.response_status
    http_response.headers["Idempotency-Replayed"] = "true"
    return response_type.model_validate(existing.response_body)


async def execute_idempotent[ResponseT: BaseModel](
    db: AsyncSession,
    *,
    http_response: Response,
    key: str | None,
    farm_id: int | None,
    actor_id: int,
    operation: str,
    payload: BaseModel,
    path_identity: Mapping[str, object],
    success_status: int,
    response_type: type[ResponseT],
    mutate: Callable[[], Awaitable[ResponseT]],
) -> ResponseT:
    """Run and commit ``mutate`` once, or replay its committed response.

    PostgreSQL arbitrates simultaneous callers at the unique index. An
    ``ON CONFLICT DO NOTHING`` contender waits for the claimant transaction:
    after a commit it reads/replays that result; after a rollback it acquires
    the claim and performs the mutation itself. This avoids both an aborted
    SQLAlchemy session race and a committed claim without domain side effects.
    """
    actor_scoped = farm_id is None
    if actor_scoped != (operation == CREATE_FARM_IDEMPOTENCY_OPERATION):
        raise ValueError(
            "farm_id may be omitted only for the actor-scoped "
            f"{CREATE_FARM_IDEMPOTENCY_OPERATION!r} operation"
        )
    if key is not None and (
        not 1 <= len(key) <= MAX_IDEMPOTENCY_KEY_LENGTH
        or any(not 0x21 <= ord(char) <= 0x7E for char in key)
    ):
        # The FastAPI header declaration normally rejects this first; retain
        # the invariant here because the service is reusable outside routers.
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key must be 1-128 printable non-whitespace ASCII characters",
        )
    if key is None:
        try:
            response = await mutate()
            await db.commit()
            http_response.status_code = success_status
            return response
        except BaseException:
            await db.rollback()
            raise

    now = utcnow()
    request_hash, accepted_request_hashes = _request_hashes(operation, payload, path_identity)
    key_digest = _sha256(key.encode("ascii"))
    expires_at = now + timedelta(hours=get_settings().idempotency_retention_hours)

    try:
        claim = (
            pg_insert(IdempotencyRecord)
            .values(
                farm_id=farm_id,
                actor_id=actor_id,
                operation=operation,
                key_digest=key_digest,
                request_hash=request_hash,
                created_at=now,
                expires_at=expires_at,
            )
            # With no explicit target PostgreSQL arbitrates against either the
            # tenant unique constraint or the NULL-farm partial unique index.
            .on_conflict_do_nothing()
            .returning(IdempotencyRecord.id)
        )
        record_id: int | None = None
        existing: IdempotencyRecord | None = None
        # Two attempts cover a cleanup race or this exact key's bounded expiry
        # removal. No request scans/deletes another tenant's retention cohort.
        for _attempt in range(2):
            record_id = (await db.execute(claim)).scalar_one_or_none()
            if record_id is not None:
                break
            existing = (
                await db.execute(
                    select(IdempotencyRecord).where(
                        IdempotencyRecord.farm_id == farm_id,
                        IdempotencyRecord.actor_id == actor_id,
                        IdempotencyRecord.operation == operation,
                        IdempotencyRecord.key_digest == key_digest,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                continue
            if existing.expires_at <= now:
                await db.delete(existing)
                await db.flush()
                existing = None
                continue
            break

        if record_id is None:
            if existing is None:
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency key changed at its expiry boundary; retry",
                )
            if not _request_hash_matches(operation, existing.request_hash, accepted_request_hashes):
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency-Key was already used with a different request",
                )
            if (
                existing.response_body is None
                or existing.response_status is None
                or existing.completed_at is None
            ):
                # Such a row cannot normally be visible: claim and result are
                # committed atomically. Fail closed if manual DB damage exists.
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency result is incomplete; retry with a new key",
                )
            body = existing.response_body
            replay_status = existing.response_status
            await db.rollback()  # read-only replay; release transaction/connection
            http_response.status_code = replay_status
            http_response.headers["Idempotency-Replayed"] = "true"
            return response_type.model_validate(body)

        response = await mutate()
        body = response.model_dump(mode="json")
        completed_at = utcnow()
        await db.execute(
            update(IdempotencyRecord)
            .where(IdempotencyRecord.id == record_id)
            .values(
                response_status=success_status,
                response_body=body,
                completed_at=completed_at,
                expires_at=completed_at
                + timedelta(hours=get_settings().idempotency_retention_hours),
            )
        )
        await db.commit()
        http_response.status_code = success_status
        return response
    except BaseException:
        await db.rollback()
        raise
