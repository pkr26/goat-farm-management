"""Preserve actual pre-cutover review facts when a finding is reviewed again."""

import asyncio
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import asyncpg
import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.db import get_sessionmaker
from app.models import Farm, ScreeningFinding, ScreeningFindingReview
from app.utils import utcnow

from .conftest import BACKEND_DIR, database_direct_url
from .test_ops_migration_integrity import _admin, _alembic, _throwaway_name
from .test_screening_clinical_integrity import _finding


async def test_review_history_id_bound_returns_404_instead_of_database_overflow(
    client: httpx.AsyncClient,
) -> None:
    owner, _finding_id = await _finding(client)
    response = await client.get(
        "/api/screening/findings/9223372036854775808/reviews", headers=owner
    )
    assert response.status_code == 404, response.text


async def test_first_rereview_retains_the_known_legacy_review(client: httpx.AsyncClient) -> None:
    owner, finding_id = await _finding(client)
    original_at = utcnow() - timedelta(days=7)
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(owner["X-Farm-Id"]))
        assert farm is not None
        original_author = farm.owner_id
        finding = await db.get(ScreeningFinding, finding_id)
        assert finding is not None
        finding.status = "CONFIRMED"
        finding.reviewed_by_id = original_author
        finding.reviewed_at = original_at
        finding.review_note = "Original veterinary observation"
        await db.commit()
    changed = await client.post(
        f"/api/screening/findings/{finding_id}/review",
        headers=owner,
        json={
            "status": "REJECTED",
            "expected_status": "CONFIRMED",
            "expected_revision": 0,
            "review_note": "Independent second opinion",
        },
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["review_revision"] == 1
    history = await client.get(f"/api/screening/findings/{finding_id}/reviews", headers=owner)
    assert history.status_code == 200, history.text
    assert history.json()["total"] == 2
    assert history.json()["legacy_review"] is True
    assert [row["revision"] for row in history.json()["reviews"]] == [1, 0]
    original = history.json()["reviews"][1]
    assert original["status"] == original["previous_status"] == "CONFIRMED"
    assert original["reviewed_by_id"] == original_author
    assert original["reviewed_at"] == original_at.isoformat()
    assert original["review_note"] == "Original veterinary observation"
    async with get_sessionmaker()() as db:
        snapshots = (
            (
                await db.execute(
                    select(ScreeningFindingReview).where(
                        ScreeningFindingReview.finding_id == finding_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(snapshots) == 2
        for statement in (
            "UPDATE screening_finding_reviews SET review_note='overwrite' "
            "WHERE finding_id=:id AND revision=0",
            "DELETE FROM screening_finding_reviews WHERE finding_id=:id AND revision=0",
        ):
            with pytest.raises(DBAPIError):
                await db.execute(text(statement), {"id": finding_id})
            await db.rollback()


async def test_populated_forward_upgrade_archives_only_known_legacy_decisions() -> None:
    database = _throwaway_name("legacy_review_snapshot")
    await _admin(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
    await _admin(f'CREATE DATABASE "{database}"')
    try:
        await _alembic(database, "upgrade", "f8e2f6a0c5d3")
        connection = await asyncpg.connect(database_direct_url(database))
        try:
            owner_id = await connection.fetchval(
                "INSERT INTO users (email, password_hash) "
                "VALUES ('legacy-review@example.test', 'unused') RETURNING id"
            )
            farm_id = await connection.fetchval(
                "INSERT INTO farms (name, owner_id) VALUES ('Legacy review farm', $1) RETURNING id",
                owner_id,
            )
            image_id = await connection.fetchval(
                "INSERT INTO screening_images (farm_id, s3_bucket, s3_key, status) "
                "VALUES ($1, 'audit-photos', 'legacy-review.jpg', 'FLAGGED') RETURNING id",
                farm_id,
            )
            run_id = await connection.fetchval(
                "INSERT INTO screening_runs (farm_id, image_id, stage, run_status, "
                "provider, model, prompt_version, error) VALUES "
                "($1, $2, 'GATE', 'ERROR', 'audit', 'audit', 'audit', 'legacy test') RETURNING id",
                farm_id,
                image_id,
            )
            original_at = utcnow() - timedelta(days=30)
            for status in ("CONFIRMED", "REJECTED", "PENDING_REVIEW"):
                await connection.execute(
                    "INSERT INTO screening_findings (farm_id, run_id, label, status, "
                    "reviewed_by_id, reviewed_at, review_note) VALUES ($1, $2, $3, $3, $4, $5, $6)",
                    farm_id,
                    run_id,
                    status,
                    owner_id if status != "PENDING_REVIEW" else None,
                    original_at if status != "PENDING_REVIEW" else None,
                    "Known original decision" if status != "PENDING_REVIEW" else None,
                )
            originals = await connection.fetch("SELECT * FROM screening_findings ORDER BY id")
        finally:
            await connection.close()
        # Stop at the revision under test. Later irreversible revisions must
        # not change where this downgrade is expected to fail.
        await _alembic(database, "upgrade", "f9a3b7c1d5e2")
        connection = await asyncpg.connect(database_direct_url(database))
        try:
            assert await connection.fetch("SELECT * FROM screening_findings ORDER BY id") == (
                originals
            )
            snapshots = await connection.fetch(
                "SELECT * FROM screening_finding_reviews ORDER BY finding_id"
            )
            assert len(snapshots) == 2
            for snapshot, original in zip(snapshots, originals[:2], strict=True):
                assert snapshot["revision"] == original["review_revision"] == 0
                assert snapshot["previous_status"] == snapshot["status"] == original["status"]
                for field in ("farm_id", "reviewed_by_id", "reviewed_at", "review_note"):
                    assert snapshot[field] == original[field]
                with pytest.raises(asyncpg.CheckViolationError):
                    await connection.execute(
                        "UPDATE screening_finding_reviews SET review_note='altered' "
                        "WHERE finding_id=$1 AND revision=0",
                        snapshot["finding_id"],
                    )
        finally:
            await connection.close()
        refused = await _alembic(database, "downgrade", "f8e2f6a0c5d3", succeeds=False)
        assert "Legacy screening review snapshots would be lost" in refused.stderr
        connection = await asyncpg.connect(database_direct_url(database))
        try:
            assert await connection.fetchval("SELECT version_num FROM alembic_version") == (
                "f9a3b7c1d5e2"
            )
            assert await connection.fetchval("SELECT count(*) FROM screening_finding_reviews") == 2
        finally:
            await connection.close()
    finally:
        await _admin(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')


async def test_forward_snapshot_lock_serializes_a_concurrent_review(tmp_path: Path) -> None:
    database = _throwaway_name("legacy_review_lock")
    await _admin(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
    await _admin(f'CREATE DATABASE "{database}"')
    locked = tmp_path / "migration-locked"
    release = tmp_path / "release-migration"
    migration: asyncio.Task[subprocess.CompletedProcess[str]] | None = None
    review: asyncio.Task[None] | None = None
    connection: asyncpg.Connection | None = None
    observer: asyncpg.Connection | None = None
    try:
        await _alembic(database, "upgrade", "f8e2f6a0c5d3")
        connection = await asyncpg.connect(database_direct_url(database))
        observer = await asyncpg.connect(database_direct_url(database))
        owner_id = await connection.fetchval(
            "INSERT INTO users (email, password_hash) "
            "VALUES ('review-lock@example.test', 'unused') RETURNING id"
        )
        farm_id = await connection.fetchval(
            "INSERT INTO farms (name, owner_id) VALUES ('Concurrent review', $1) RETURNING id",
            owner_id,
        )
        image_id = await connection.fetchval(
            "INSERT INTO screening_images (farm_id, s3_bucket, s3_key, status) "
            "VALUES ($1, 'audit-photos', 'review-lock.jpg', 'FLAGGED') RETURNING id",
            farm_id,
        )
        run_id = await connection.fetchval(
            "INSERT INTO screening_runs (farm_id, image_id, stage, run_status, "
            "provider, model, prompt_version, error) VALUES "
            "($1, $2, 'GATE', 'ERROR', 'audit', 'audit', 'audit', 'fixture') RETURNING id",
            farm_id,
            image_id,
        )
        original_at = utcnow() - timedelta(days=30)
        finding_id = await connection.fetchval(
            "INSERT INTO screening_findings (farm_id, run_id, label, status, "
            "reviewed_by_id, reviewed_at, review_note) VALUES "
            "($1, $2, 'legacy', 'CONFIRMED', $3, $4, 'Original decision') RETURNING id",
            farm_id,
            run_id,
            owner_id,
            original_at,
        )
        # Pause the real Alembic upgrade only after its initial Finding lock.
        # The reviewer follows the deployed f8 contract: lock Finding, insert
        # its next immutable History row, then update the current projection.
        script = """
import sys, time
from pathlib import Path
from alembic import command, op
from alembic.config import Config
original_execute = op.execute
def pause_after_finding_lock(statement, *args, **kwargs):
    result = original_execute(statement, *args, **kwargs)
    if str(statement).startswith('LOCK TABLE screening_findings '):
        Path(sys.argv[1]).write_text('locked')
        deadline = time.monotonic() + 15
        while not Path(sys.argv[2]).exists():
            if time.monotonic() > deadline:
                raise TimeoutError('test did not release migration')
            time.sleep(0.01)
    return result
op.execute = pause_after_finding_lock
command.upgrade(Config('alembic.ini'), 'f9a3b7c1d5e2')
"""
        env = os.environ.copy()
        target_url = database_direct_url(database).replace(
            "postgresql://", "postgresql+asyncpg://", 1
        )
        env["GOATFARM_DATABASE_URL"] = target_url
        env["GOATFARM_MIGRATION_DATABASE_URL"] = target_url
        # The test deliberately pauses the quiescence-gated revision in a
        # disposable database and coordinates its only concurrent writer.
        env["GOATFARM_MIGRATION_WRITES_QUIESCED"] = "true"
        migration = asyncio.create_task(
            asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-c", script, str(locked), str(release)],
                cwd=BACKEND_DIR,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        )
        async with asyncio.timeout(15):
            while not locked.exists():
                assert not migration.done(), "upgrade exited before its Finding table lock"
                await asyncio.sleep(0.01)
        selected = asyncio.Event()
        inserted = asyncio.Event()
        reviewer = connection

        async def write_review() -> None:
            async with reviewer.transaction():
                await reviewer.execute("SET LOCAL statement_timeout = '10s'")
                await reviewer.fetchrow(
                    "SELECT * FROM screening_findings WHERE id=$1 FOR UPDATE", finding_id
                )
                selected.set()
                await reviewer.execute(
                    "INSERT INTO screening_finding_reviews (finding_id, revision, farm_id, "
                    "previous_status, status, review_note, reviewed_by_id, reviewed_at) "
                    "VALUES ($1, 1, $2, 'CONFIRMED', 'REJECTED', 'Next decision', $3, $4)",
                    finding_id,
                    farm_id,
                    owner_id,
                    utcnow(),
                )
                inserted.set()
                await reviewer.execute(
                    "UPDATE screening_findings SET status='REJECTED', review_revision=1, "
                    "review_note='Next decision', reviewed_at=$2 WHERE id=$1",
                    finding_id,
                    utcnow(),
                )

        review = asyncio.create_task(write_review())
        async with asyncio.timeout(5):
            while True:
                row_share = await observer.fetchrow(
                    "SELECT granted FROM pg_locks WHERE pid=$1 AND mode='RowShareLock' "
                    "AND relation='screening_findings'::regclass",
                    reviewer.get_server_pid(),
                )
                if row_share is not None:
                    break
                await asyncio.sleep(0.01)
        blocks_reviewer = not row_share["granted"]
        if not blocks_reviewer:
            # Make the unsafe interleaving deterministic before allowing DDL.
            await asyncio.wait_for(inserted.wait(), timeout=5)
        selected_before_release = selected.is_set()
        # EXCLUSIVE continues to permit ordinary clinical readers.
        assert await observer.fetchval("SELECT count(*) FROM screening_findings") == 1
        release.write_text("continue")
        migration_result, review_result = await asyncio.gather(
            migration, review, return_exceptions=True
        )
        assert isinstance(migration_result, subprocess.CompletedProcess)
        assert blocks_reviewer and not selected_before_release, (
            "Finding lock admitted a reviewer before History DDL; "
            f"migration={migration_result.stderr}; reviewer={review_result!r}"
        )
        assert migration_result.returncode == 0, migration_result.stderr
        assert review_result is None, repr(review_result)
        rows = await observer.fetch(
            "SELECT revision, status, review_note, reviewed_at "
            "FROM screening_finding_reviews ORDER BY revision"
        )
        assert [(row["revision"], row["status"], row["review_note"]) for row in rows] == [
            (0, "CONFIRMED", "Original decision"),
            (1, "REJECTED", "Next decision"),
        ]
        assert rows[0]["reviewed_at"] == original_at
        assert await observer.fetchval("SELECT version_num FROM alembic_version") == (
            "f9a3b7c1d5e2"
        )
        assert (
            await observer.fetchval(
                "SELECT review_revision FROM screening_findings WHERE id=$1", finding_id
            )
            == 1
        )
    finally:
        release.write_text("continue")
        pending = [task for task in (migration, review) if task is not None]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if observer is not None:
            await observer.close()
        if connection is not None:
            await connection.close()
        await _admin(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
