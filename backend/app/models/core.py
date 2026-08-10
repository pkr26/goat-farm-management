"""Multi-tenancy: users, farms, roles, memberships, refresh sessions."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ..utils import utcnow

if TYPE_CHECKING:
    from .animals import Animal

logger = logging.getLogger("goatfarm.models")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "deleted_at IS NULL OR (name IS NULL AND email LIKE 'deleted-%@deleted.invalid')",
            name="ck_users_deleted_profile_scrubbed",
        ),
        Index(
            "ix_users_deleted_id",
            "id",
            postgresql_where=text("deleted_at IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    # Included in access JWTs and checked on every authenticated request.
    # Password changes/resets and logout increment the value so already-issued
    # bearer tokens stop working immediately. Farm-membership deactivation is
    # tenant-local and is enforced by the membership lookup instead; it must
    # not log the same global account out of unrelated farms.
    token_version: Mapped[int] = mapped_column(default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    # Account deletion keeps a pseudonymous row so immutable farm/audit
    # attribution survives. Authentication dependencies reject tombstones;
    # deletion scrubs the email, name and reusable password material.
    deleted_at: Mapped[datetime | None]

    farms: Mapped[list[Farm]] = relationship(back_populates="owner")
    memberships: Mapped[list[FarmMembership]] = relationship(back_populates="user")

    @property
    def display_name(self) -> str:
        if self.deleted_at is not None:
            return "Deleted account"
        return self.name or self.email


class Farm(Base):
    __tablename__ = "farms"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    location: Mapped[str | None] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(
        String(64), default="Asia/Kolkata", server_default="Asia/Kolkata"
    )
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
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
    __table_args__ = (
        UniqueConstraint("farm_id", "id", name="uq_roles_farm_id_id"),
        Index(
            "uq_roles_farm_active_name",
            "farm_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    code: Mapped[str | None] = mapped_column(String(30))  # preset key; null for custom roles
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(String(255))
    permissions: Mapped[str] = mapped_column(Text, default="[]")  # JSON list of codes
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    # Custom-role deletion is a tombstone, not an unbounded rewrite of every
    # historical Task that referenced the role. Active role queries exclude
    # tombstones; task audit rows may keep this immutable identity/name.
    deleted_at: Mapped[datetime | None]

    farm: Mapped[Farm] = relationship(back_populates="roles")
    memberships: Mapped[list[FarmMembership]] = relationship(
        back_populates="role", foreign_keys="FarmMembership.role_id"
    )

    def permission_set(self) -> set[str]:
        if self.deleted_at is not None:
            return set()
        import json

        try:
            decoded = json.loads(self.permissions or "[]")
        except ValueError:
            decoded = None
        # The shape matters as much as the syntax: valid JSON that is not a
        # list ("null", "5") would raise TypeError past this guard, and a bare
        # string would silently decompose into its characters.
        if not isinstance(decoded, list):
            # Fail-closed is right, but not silently: a corrupt
            # row locks the role's workers out of everything.
            logger.warning("Role id=%s has corrupt permissions JSON — treating as empty", self.id)
            return set()
        return {p for p in decoded if isinstance(p, str)}


class FarmMembership(Base):
    """Links a User to a Farm as a worker with a Role. The farm owner is not
    a membership — ownership (Farm.owner_id) implies all permissions."""

    __tablename__ = "farm_memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "farm_id", name="uq_membership_user_farm"),
        UniqueConstraint("farm_id", "user_id", name="uq_farm_memberships_farm_user"),
        Index(
            "ix_farm_memberships_active_user_id_id",
            "user_id",
            "id",
            postgresql_where=text("is_active IS TRUE"),
        ),
        ForeignKeyConstraint(
            ["farm_id", "role_id"],
            ["roles.farm_id", "roles.id"],
            name="fk_farm_memberships_farm_role",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id", ondelete="CASCADE"), index=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="RESTRICT"), index=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    # True only when this farm created the underlying global User. Legacy and
    # externally registered accounts fail closed: their password may not be
    # rewritten by a farm manager.
    account_provisioned_by_farm: Mapped[bool] = mapped_column(default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    user: Mapped[User] = relationship(back_populates="memberships")
    farm: Mapped[Farm] = relationship(back_populates="memberships")
    role: Mapped[Role] = relationship(back_populates="memberships", foreign_keys=[role_id])


class RefreshSession(Base):
    """Server-side record of one issued refresh token.

    Every refresh JWT's jti lands here at issue time; /refresh consumes the
    presented row and inserts its successor into the same rotation family.
    Re-presenting a consumed or revoked jti means a rotated-away token was
    replayed — theft per RFC 6819 §5.2.2.3 — so the whole family is revoked.
    Logout, password change, and owner-initiated worker password resets revoke
    rows outright. A farm-membership deactivation is intentionally tenant-local
    and leaves the account's global refresh families intact. The jti alone is
    useless without the signing key, so it is stored plain (not hashed).
    """

    __tablename__ = "refresh_sessions"
    __table_args__ = (
        Index("ix_refresh_sessions_expires_id", "expires_at", "id"),
        Index("ix_refresh_sessions_family_created_id", "family_id", "created_at", "id"),
        Index("ix_refresh_sessions_user_created_id", "user_id", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    family_id: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column()
    consumed_at: Mapped[datetime | None] = mapped_column()
    # The successor JTI makes a very short concurrent replay idempotent: two
    # tabs presenting the same cookie receive the exact same replacement
    # token instead of falsely triggering family-wide theft revocation.
    replacement_jti: Mapped[str | None] = mapped_column(String(64))
    revoked_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
