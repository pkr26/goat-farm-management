"""make uq_kid_entries_farm_tag usable by the animal-tag trigger

The index predicate carried a second arm, ``btrim(tag) <> ''``. PostgreSQL's
predicate-implication prover can derive ``tag IS NOT NULL`` from the strict
``tag = $1`` but not that one, so the index was never eligible for the only
query that needs it: the BEFORE INSERT OR UPDATE trigger on ``animals``
(``enforce_animal_tag_against_stillborns``) looking for a matching STILLBORN
tag in the same farm. Every animal insert therefore scanned the farm's entire
``kid_entries`` table — measured on 20,000 rows: a sequential scan touching
748 buffers (~2.5 ms) instead of 3 buffers (~0.014 ms) through the index, and
that cost is paid once per created animal (a 1,000-head purchase pays it
1,000 times).

Dropping the arm is safe: every writer stores ``tag or None``
(services/kidding.py), so a blank tag never lands. It is also a strict
tightening — rows whose tag is whitespace-only were previously excluded from
the uniqueness guarantee and are now covered — so the upgrade verifies that
no such duplicate already exists rather than failing mid-DDL.

Revision ID: c3d4e5f6a7b1
Revises: b1c2d3e4f5a6
"""

from alembic import op

revision = "c3d4e5f6a7b1"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None

INDEX_NAME = "uq_kid_entries_farm_tag"
OLD_WHERE = "tag IS NOT NULL AND btrim(tag) <> ''"
NEW_WHERE = "tag IS NOT NULL"


def _rebuild(where: str) -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
    op.execute(f"CREATE UNIQUE INDEX {INDEX_NAME} ON kid_entries (farm_id, tag) WHERE {where}")


def upgrade() -> None:
    # The widened predicate covers whitespace-only tags for the first time.
    # Report any pre-existing collision explicitly instead of failing inside
    # CREATE UNIQUE INDEX with only a key value to go on.
    conflicting = (
        op.get_bind()
        .exec_driver_sql(
            "SELECT farm_id, tag, count(*) FROM kid_entries "
            "WHERE tag IS NOT NULL AND btrim(tag) = '' "
            "GROUP BY farm_id, tag HAVING count(*) > 1"
        )
        .fetchall()
    )
    if conflicting:
        raise RuntimeError(
            "Cannot widen uq_kid_entries_farm_tag: duplicate blank kid tags exist "
            f"for {conflicting!r}. Give those entries distinct tags (or NULL) first."
        )
    _rebuild(NEW_WHERE)


def downgrade() -> None:
    _rebuild(OLD_WHERE)
