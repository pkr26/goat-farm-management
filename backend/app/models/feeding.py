"""Feeding."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ..utils import today


class FeedRecipe(Base):
    __tablename__ = "feed_recipes"
    __table_args__ = (
        CheckConstraint("btrim(code) <> ''", name="ck_feed_recipes_code_nonblank"),
        CheckConstraint("btrim(name) <> ''", name="ck_feed_recipes_name_nonblank"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(String(255))

    lines: Mapped[list[FeedRecipeLine]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan"
    )


class FeedRecipeLine(Base):
    __tablename__ = "feed_recipe_lines"
    __table_args__ = (
        CheckConstraint(
            "kg_per_100kg > 0 AND kg_per_100kg <= 100 AND "
            "kg_per_100kg::text NOT IN ('NaN', 'Infinity', '-Infinity')",
            name="ck_feed_recipe_lines_kg",
        ),
        CheckConstraint(
            "category IN ('ROUGHAGE_WET', 'ROUGHAGE_DRY', 'CONCENTRATE')",
            name="ck_feed_recipe_lines_category",
        ),
        CheckConstraint(
            "btrim(ingredient) <> ''",
            name="ck_feed_recipe_lines_ingredient_nonblank",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("feed_recipes.id"), index=True)
    ingredient: Mapped[str] = mapped_column(String(120))
    kg_per_100kg: Mapped[float]
    category: Mapped[str] = mapped_column(String(20))  # IngredientCategory enum

    recipe: Mapped[FeedRecipe] = relationship(back_populates="lines")


class FeedInventory(Base):
    __tablename__ = "feed_inventory"
    # One stock row per (farm, ingredient) — mix_feed_batch/add_feed_stock
    # look the row up by that pair, so a duplicate would split the balance.
    __table_args__ = (
        UniqueConstraint("farm_id", "ingredient", name="uq_feed_inventory_farm_ingredient"),
        # Transaction provenance uses a composite farm/id FK so a ledger row
        # can never point at another farm's stock item.
        UniqueConstraint("farm_id", "id", name="uq_feed_inventory_farm_id_id"),
        CheckConstraint(
            "category IN ('ROUGHAGE_WET', 'ROUGHAGE_DRY', 'CONCENTRATE')",
            name="ck_feed_inventory_category",
        ),
        CheckConstraint("unit = 'kg'", name="ck_feed_inventory_unit"),
        CheckConstraint(
            "qty_on_hand >= 0 AND qty_on_hand::text NOT IN ('NaN', 'Infinity', '-Infinity')",
            name="ck_feed_inventory_qty",
        ),
        CheckConstraint(
            "reorder_level IS NULL OR "
            "(reorder_level >= 0 AND "
            "reorder_level::text NOT IN ('NaN', 'Infinity', '-Infinity'))",
            name="ck_feed_inventory_reorder_level",
        ),
        CheckConstraint(
            "last_purchase_price_per_kg IS NULL OR "
            "last_purchase_price_per_kg BETWEEN 0 AND 1000000000",
            name="ck_feed_inventory_last_price",
        ),
        CheckConstraint(
            "btrim(ingredient) <> ''",
            name="ck_feed_inventory_ingredient_nonblank",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    ingredient: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(20))  # IngredientCategory enum
    unit: Mapped[str] = mapped_column(String(10), default="kg")
    # Stock is accounted in whole grams.  ``asdecimal=False`` preserves the
    # established float API/ORM contract while PostgreSQL, rather than binary
    # floating point, remains the authoritative exact ledger.
    qty_on_hand: Mapped[float] = mapped_column(Numeric(15, 3, asdecimal=False), default=0.0)
    reorder_level: Mapped[float | None] = mapped_column(Numeric(15, 3, asdecimal=False))
    last_purchase_price_per_kg: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))


class FeedFinishedStock(Base):
    """Farm-scoped stock of a recipe after its ingredients have been mixed.

    Ingredient inventory and ready-to-dispense feed are deliberately separate:
    mixing consumes the former and creates the latter; dispensing consumes the
    latter.  Without this row a successful mix disappeared from inventory and
    operators could dispense unlimited quantities that had never been made.
    """

    __tablename__ = "feed_finished_stock"
    __table_args__ = (
        UniqueConstraint("farm_id", "recipe_code", name="uq_finished_feed_farm_recipe"),
        CheckConstraint("qty_on_hand >= 0", name="ck_finished_feed_qty_nonneg"),
        CheckConstraint(
            "qty_on_hand::text NOT IN ('NaN', 'Infinity', '-Infinity')",
            name="ck_finished_feed_qty_finite",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    recipe_code: Mapped[str] = mapped_column(ForeignKey("feed_recipes.code"), index=True)
    qty_on_hand: Mapped[float] = mapped_column(Numeric(15, 3, asdecimal=False), default=0.0)


class FeedingRecord(Base):
    __tablename__ = "feeding_records"
    __table_args__ = (
        CheckConstraint(
            "shift IN ('MORNING', 'AFTERNOON', 'NIGHT')",
            name="ck_feeding_records_shift",
        ),
        CheckConstraint(
            "bucket IN "
            "('QUARANTINE', 'FOUNDATION', 'BREEDING', 'PREGNANCY_EARLY', "
            "'PREGNANCY_LATE', 'DELIVERY', 'RECOVERY', 'RESTING', "
            "'MALE_KIDS', 'FEMALE_KIDS')",
            name="ck_feeding_records_bucket",
        ),
        CheckConstraint(
            "qty_kg > 0 AND qty_kg <= 1000000 AND "
            "qty_kg::text NOT IN ('NaN', 'Infinity', '-Infinity')",
            name="ck_feeding_records_qty",
        ),
        CheckConstraint(
            "recipe_code IS NULL OR btrim(recipe_code) <> ''",
            name="ck_feeding_records_recipe_code",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    date: Mapped[date] = mapped_column(default=today)
    shift: Mapped[str] = mapped_column(String(10))  # FeedingShift enum
    bucket: Mapped[str] = mapped_column(String(20))  # Bucket enum
    recipe_code: Mapped[str | None] = mapped_column(String(30))
    qty_kg: Mapped[float] = mapped_column(Numeric(15, 3, asdecimal=False))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


Index(
    "ix_feeding_records_farm_date_id",
    FeedingRecord.farm_id,
    FeedingRecord.date,
    FeedingRecord.id,
)
Index(
    "ix_feeding_records_farm_date_allocation",
    FeedingRecord.farm_id,
    FeedingRecord.date,
    FeedingRecord.bucket,
    FeedingRecord.recipe_code,
    FeedingRecord.shift,
    postgresql_include=["qty_kg"],
)
