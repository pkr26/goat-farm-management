"""Persist screening retention deletion intent before object removal.

Revision ID: fd4e5f6a7b8c
Revises: fc3d4e5f6a7b
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "fd4e5f6a7b8c"
down_revision: str | Sequence[str] | None = "fc3d4e5f6a7b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SCREENING_LEASE_COLUMNS = (
    "id",
    "farm_id",
    "bucket",
    "batch_id",
    "s3_bucket",
    "s3_key",
    "upload_content_type",
    "upload_token",
    "sha256",
    "byte_size",
    "width",
    "height",
    "normalized_key",
    "captured_date",
    "status",
    "error",
    "next_attempt_at",
    "screening_attempts",
    "created_at",
)


def _scope_screening_updated_at_trigger() -> None:
    """Keep privacy bookkeeping from moving the worker lease clock.

    ``updated_at`` drives PROCESSING stale-claim and ERROR retry timing.  The
    earlier all-column trigger correctly covered out-of-band pipeline writes,
    but the independent raw-cleanup saga and retention tombstone are not
    screening progress. Restrict the trigger to the pre-existing pipeline
    columns; explicit ``updated_at`` heartbeats remain explicit and therefore
    need no trigger assistance.
    """
    columns = ", ".join(_SCREENING_LEASE_COLUMNS)
    op.execute("DROP TRIGGER trg_screening_images_updated_at_refresh ON screening_images")
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_screening_images_updated_at_refresh "
            f"BEFORE UPDATE OF {columns} ON screening_images "
            "FOR EACH ROW EXECUTE FUNCTION refresh_screening_images_updated_at()"
        )
    )


def _restore_all_column_screening_updated_at_trigger() -> None:
    """Restore the exact pre-fd trigger scope during downgrade."""
    op.execute("DROP TRIGGER trg_screening_images_updated_at_refresh ON screening_images")
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_screening_images_updated_at_refresh "
            "BEFORE UPDATE ON screening_images "
            "FOR EACH ROW EXECUTE FUNCTION refresh_screening_images_updated_at()"
        )
    )


def _add_validated_check(name: str, condition: str) -> None:
    """Enforce new writes first, then validate the populated table."""
    op.create_check_constraint(
        name,
        "screening_images",
        condition,
        postgresql_not_valid=True,
    )
    op.execute(sa.text(f'ALTER TABLE screening_images VALIDATE CONSTRAINT "{name}"'))


def upgrade() -> None:
    # A raw object can be recreated with the already-issued browser form
    # after the screening worker's eager delete.  Persist a second cleanup
    # obligation on the image row itself; it survives every screening status
    # (including retry exhaustion) and is acknowledged only after the form's
    # write window has closed and every object version has been purged.
    cleanup_default = sa.text("timezone('UTC', now()) + interval '25 hours'")
    op.add_column(
        "screening_images",
        sa.Column("raw_cleanup_after", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "screening_images",
        sa.Column(
            "raw_cleanup_next_attempt_at",
            sa.DateTime(),
            nullable=True,
        ),
    )
    op.add_column(
        "screening_images",
        sa.Column(
            "raw_cleanup_attempts",
            sa.BigInteger(),
            nullable=True,
        ),
    )
    op.add_column(
        "screening_images",
        sa.Column("raw_cleanup_completed_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "screening_images",
        sa.Column("raw_cleanup_last_error", sa.String(length=255), nullable=True),
    )
    # The existing all-column trigger treats any UPDATE as screening progress
    # and would rewrite every row's worker lease timestamp during the backfill.
    # Narrow it first. This revision is explicitly write-quiescence gated, so
    # old workers cannot race the trigger contract change.
    _scope_screening_updated_at_trigger()
    # The historical rows pre-date the exact per-form deadline.  Use the
    # maximum accepted presign lifetime (24h) plus the one-hour safety slack
    # for anything that may have had a browser form. An active tokenless,
    # never-normalized legacy-discovery row still has raw bytes as its sole
    # processing input, so give the worker that same conservative 25-hour
    # migration grace. Terminal/attempt-exhausted legacy rows have no reusable
    # form and no remaining model work; those are safely due immediately. The
    # scoped trigger above preserves every old ``updated_at`` value during this
    # bookkeeping-only UPDATE. ``5`` is the pipeline's historical and current
    # MAX_SCREENING_ATTEMPTS boundary at this schema revision.
    op.execute(
        """
        UPDATE screening_images
        SET raw_cleanup_after = CASE
                WHEN upload_token IS NULL AND normalized_key IS NULL
                     AND status IN ('PENDING', 'PROCESSING', 'ERROR', 'FLAGGED')
                     AND screening_attempts < 5
                    THEN timezone('UTC', now()) + interval '25 hours'
                WHEN upload_token IS NULL AND normalized_key IS NULL
                    THEN timezone('UTC', now())
                ELSE created_at + interval '25 hours'
            END,
            raw_cleanup_next_attempt_at = CASE
                WHEN upload_token IS NULL AND normalized_key IS NULL
                     AND status IN ('PENDING', 'PROCESSING', 'ERROR', 'FLAGGED')
                     AND screening_attempts < 5
                    THEN timezone('UTC', now()) + interval '25 hours'
                WHEN upload_token IS NULL AND normalized_key IS NULL
                    THEN timezone('UTC', now())
                ELSE created_at + interval '25 hours'
            END,
            raw_cleanup_attempts = 0
        """
    )
    # A validated simple CHECK lets PostgreSQL prove each SET NOT NULL without
    # performing an additional full-table null scan. The checks are temporary;
    # the column metadata remains the canonical end-state invariant.
    _add_validated_check(
        "ck_screening_images_raw_cleanup_after_not_null_migration",
        "raw_cleanup_after IS NOT NULL",
    )
    _add_validated_check(
        "ck_screening_images_raw_cleanup_attempts_not_null_migration",
        "raw_cleanup_attempts IS NOT NULL",
    )
    op.alter_column(
        "screening_images",
        "raw_cleanup_after",
        nullable=False,
        server_default=cleanup_default,
    )
    op.alter_column(
        "screening_images",
        "raw_cleanup_next_attempt_at",
        server_default=cleanup_default,
    )
    op.alter_column(
        "screening_images",
        "raw_cleanup_attempts",
        nullable=False,
        server_default=sa.text("0"),
    )
    op.drop_constraint(
        "ck_screening_images_raw_cleanup_attempts_not_null_migration",
        "screening_images",
        type_="check",
    )
    op.drop_constraint(
        "ck_screening_images_raw_cleanup_after_not_null_migration",
        "screening_images",
        type_="check",
    )
    _add_validated_check(
        "ck_screening_images_raw_cleanup_attempts_nonneg",
        "raw_cleanup_attempts >= 0",
    )
    _add_validated_check(
        "ck_screening_images_raw_cleanup_state",
        "(raw_cleanup_completed_at IS NULL AND "
        "raw_cleanup_next_attempt_at IS NOT NULL AND "
        "raw_cleanup_next_attempt_at >= raw_cleanup_after) OR "
        "(raw_cleanup_completed_at IS NOT NULL AND "
        "raw_cleanup_completed_at >= raw_cleanup_after AND "
        "raw_cleanup_attempts > 0 AND "
        "raw_cleanup_next_attempt_at IS NULL AND raw_cleanup_last_error IS NULL)",
    )
    _add_validated_check(
        "ck_screening_images_raw_cleanup_error_bounded",
        "raw_cleanup_last_error IS NULL OR "
        "(btrim(raw_cleanup_last_error) <> '' AND "
        "length(raw_cleanup_last_error) <= 255)",
    )
    op.add_column(
        "screening_images",
        sa.Column("retention_tombstoned_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "screening_retention_deletions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("farm_id", sa.Integer(), nullable=False),
        sa.Column("image_id", sa.BigInteger(), nullable=False),
        sa.Column("s3_bucket", sa.String(length=255), nullable=False),
        sa.Column("object_keys", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "preserved_keys",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "deleted_keys",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=20), server_default="PENDING", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failure_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(), nullable=True),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(),
            server_default=sa.text("timezone('UTC', now())"),
            nullable=True,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("objects_deleted_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("timezone('UTC', now())"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("timezone('UTC', now())"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND failure_count >= 0 "
            "AND failure_count <= attempt_count AND "
            "((attempt_count = 0 AND last_attempt_at IS NULL) OR "
            "(attempt_count > 0 AND last_attempt_at IS NOT NULL))",
            name="ck_screening_retention_deletions_attempts",
        ),
        sa.CheckConstraint(
            "(last_error IS NULL AND failure_count = 0) OR "
            "(last_error IS NOT NULL AND failure_count > 0)",
            name="ck_screening_retention_deletions_failure_state",
        ),
        sa.CheckConstraint(
            "btrim(s3_bucket) <> ''",
            name="ck_screening_retention_deletions_bucket_nonblank",
        ),
        sa.CheckConstraint(
            "last_error IS NULL OR btrim(last_error) <> ''",
            name="ck_screening_retention_deletions_error_nonblank",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(object_keys) = 'array' AND jsonb_array_length(object_keys) > 0",
            name="ck_screening_retention_deletions_object_keys_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(preserved_keys) = 'array'",
            name="ck_screening_retention_deletions_preserved_keys_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(deleted_keys) = 'array'",
            name="ck_screening_retention_deletions_deleted_keys_array",
        ),
        sa.CheckConstraint(
            "(status = 'PENDING' AND objects_deleted_at IS NULL "
            "AND next_attempt_at IS NOT NULL) OR "
            "(status = 'OBJECTS_DELETED' AND objects_deleted_at IS NOT NULL "
            "AND next_attempt_at IS NULL AND last_error IS NULL)",
            name="ck_screening_retention_deletions_state",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'OBJECTS_DELETED')",
            name="ck_screening_retention_deletions_status",
        ),
        sa.ForeignKeyConstraint(
            ["farm_id", "image_id"],
            ["screening_images.farm_id", "screening_images.id"],
            name="fk_screening_retention_deletions_image",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "farm_id", "image_id", name="uq_screening_retention_deletions_farm_image"
        ),
    )
    op.create_index(
        "ix_screening_retention_deletions_farm_status_due_id",
        "screening_retention_deletions",
        ["farm_id", "status", "next_attempt_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    # An intent may describe objects already removed even while its status is
    # still PENDING (crash between S3 success and acknowledgement). Dropping
    # any row would therefore destroy the only safe retry/finalization state.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM screening_retention_deletions) THEN
                RAISE EXCEPTION
                    'Cannot downgrade: screening retention deletions are pending finalization';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM screening_images
                WHERE raw_cleanup_completed_at IS NULL
            ) THEN
                RAISE EXCEPTION
                    'Cannot downgrade: raw screening cleanup obligations are outstanding';
            END IF;
        END $$
        """
    )
    op.drop_index(
        "ix_screening_retention_deletions_farm_status_due_id",
        table_name="screening_retention_deletions",
    )
    op.drop_table("screening_retention_deletions")
    op.drop_constraint(
        "ck_screening_images_raw_cleanup_error_bounded",
        "screening_images",
        type_="check",
    )
    op.drop_constraint(
        "ck_screening_images_raw_cleanup_state",
        "screening_images",
        type_="check",
    )
    op.drop_constraint(
        "ck_screening_images_raw_cleanup_attempts_nonneg",
        "screening_images",
        type_="check",
    )
    op.drop_column("screening_images", "raw_cleanup_last_error")
    op.drop_column("screening_images", "raw_cleanup_completed_at")
    op.drop_column("screening_images", "raw_cleanup_attempts")
    op.drop_column("screening_images", "raw_cleanup_next_attempt_at")
    op.drop_column("screening_images", "raw_cleanup_after")
    # Drop the same-row retention fence last: the intent table above is the
    # only owner allowed to remove an aged chain while this column exists.
    op.drop_column("screening_images", "retention_tombstoned_at")
    _restore_all_column_screening_updated_at_trigger()
