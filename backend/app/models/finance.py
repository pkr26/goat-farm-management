"""Finance."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ..utils import today

if TYPE_CHECKING:
    from .animals import Animal


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    date: Mapped[date] = mapped_column(default=today, index=True)
    type: Mapped[str] = mapped_column(String(10))  # TransactionType enum
    category: Mapped[str] = mapped_column(String(20))  # TransactionCategory enum
    amount: Mapped[float]
    related_animal_id: Mapped[int | None] = mapped_column(ForeignKey("animals.id"))
    notes: Mapped[str | None] = mapped_column(String(255))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    related_animal: Mapped[Animal | None] = relationship()
