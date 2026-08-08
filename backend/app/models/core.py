"""Multi-tenancy: users, farms, roles, memberships, refresh sessions."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ..utils import utcnow

if TYPE_CHECKING:
    from .animals import Animal

logger = logging.getLogger("goatfarm.models")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    farms: Mapped[list[Farm]] = relationship(back_populates="owner")
    memberships: Mapped[list[FarmMembership]] = relationship(back_populates="user")

    @property
    def display_name(self) -> str:
        return self.name or self.email


class Farm(Base):
    __tablename__ = "farms"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    location: Mapped[str | None] = mapped_column(String(120))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    owner: Mapped[User] = relationship(back_populates="farms")
    animals: Mapped[list[Animal]] = relationship(back_populates="farm")
    roles: Mapped[list[Role]] = relationship(back_populates="farm", cascade="all, delete-orphan")
    memberships: Mapped[list[FarmMembership]] = relationship(
        back_populates="farm", cascade="all, delete-orphan"
    )


class Role(Base):
    """Farm-scoped role: a named bundle of permission codes (JSON list).

    Preset roles carry a stable `code` (MOVER, VET, ...) used to map
    auto-generated tasks to a default assignee; custom roles have code=None.
    """

    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("farm_id", "name", name="uq_role_name_per_farm"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    code: Mapped[str | None] = mapped_column(String(30))  # preset key; null for custom roles
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(String(255))
    permissions: Mapped[str] = mapped_column(Text, default="[]")  # JSON list of codes
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    farm: Mapped[Farm] = relationship(back_populates="roles")
    memberships: Mapped[list[FarmMembership]] = relationship(back_populates="role")

    def permission_set(self) -> set[str]:
        import json

        try:
            return {p for p in json.loads(self.permissions or "[]") if isinstance(p, str)}
        except ValueError:
            # Fail-closed is right, but not silently (AUDIT 4-L1): a corrupt
            # row locks the role's workers out of everything.
            logger.warning("Role id=%s has corrupt permissions JSON — treating as empty", self.id)
            return set()


class FarmMembership(Base):
    """Links a User to a Farm as a worker with a Role. The farm owner is not
    a membership — ownership (Farm.owner_id) implies all permissions."""

    __tablename__ = "farm_memberships"
    __table_args__ = (UniqueConstraint("user_id", "farm_id", name="uq_membership_user_farm"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), index=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    user: Mapped[User] = relationship(back_populates="memberships")
    farm: Mapped[Farm] = relationship(back_populates="memberships")
    role: Mapped[Role] = relationship(back_populates="memberships")


class RefreshSession(Base):
    """Server-side record of one issued refresh token.

    Every refresh JWT's jti lands here at issue time; /refresh consumes the
    presented row and inserts its successor into the same rotation family.
    Re-presenting a consumed or revoked jti means a rotated-away token was
    replayed — theft per RFC 6819 §5.2.2.3 — so the whole family is revoked.
    Logout, password change, owner-initiated worker password resets, and
    membership deactivation revoke rows outright. The jti alone is useless
    without the signing key, so it is stored plain (not hashed).
    """

    __tablename__ = "refresh_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    family_id: Mapped[str] = mapped_column(String(64), index=True)
    expires_at: Mapped[datetime] = mapped_column()
    consumed_at: Mapped[datetime | None] = mapped_column()
    revoked_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
