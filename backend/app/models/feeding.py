"""Feeding."""

from __future__ import annotations

from datetime import date

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ..utils import today


class FeedRecipe(Base):
    __tablename__ = "feed_recipes"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(String(255))

    lines: Mapped[list[FeedRecipeLine]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan"
    )


class FeedRecipeLine(Base):
    __tablename__ = "feed_recipe_lines"

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
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    ingredient: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(20))  # IngredientCategory enum
    unit: Mapped[str] = mapped_column(String(10), default="kg")
    qty_on_hand: Mapped[float] = mapped_column(default=0.0)
    reorder_level: Mapped[float | None]
    last_purchase_price_per_kg: Mapped[float | None]


class FeedingRecord(Base):
    __tablename__ = "feeding_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    date: Mapped[date] = mapped_column(default=today)
    shift: Mapped[str] = mapped_column(String(10))  # FeedingShift enum
    bucket: Mapped[str] = mapped_column(String(20))  # Bucket enum
    recipe_code: Mapped[str | None] = mapped_column(String(30))
    qty_kg: Mapped[float]
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
