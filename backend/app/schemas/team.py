"""Pydantic schemas for the team module (workers, roles, permissions)."""

from pydantic import BaseModel, ConfigDict, Field

from .auth import MAX_EMAIL_LENGTH, EmailMixin
from .common import BoundedId, PostgresText, StrictBool, StrictInputModel


class WorkerCreateIn(EmailMixin):
    email: str = Field(max_length=MAX_EMAIL_LENGTH)
    password: str | None = Field(default=None, max_length=128)  # required for a new account
    name: PostgresText | None = Field(default=None, max_length=120)
    role_id: BoundedId


class RoleChangeIn(StrictInputModel):
    role_id: BoundedId


class WorkerStatusIn(StrictInputModel):
    # StrictBool, like every other mutating boolean input: lax coercion would
    # let `1`/`"false"`/`"off"` silently flip a worker's access instead of
    # returning the 422 the strict-input contract promises.
    is_active: StrictBool


class PasswordResetIn(StrictInputModel):
    password: str = Field(min_length=1, max_length=128)


class MembershipOut(BaseModel):
    id: int
    user_id: int
    email: str
    name: str | None
    role_id: int | None
    role_name: str | None
    is_active: bool
    can_reset_password: bool
    reset_password_block_reason: str | None


class RoleIn(StrictInputModel):
    name: PostgresText = Field(min_length=1, max_length=80)
    description: PostgresText | None = Field(default=None, max_length=255)
    permissions: list[str] = Field(default_factory=list, max_length=100)


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str | None  # preset key (MOVER/VET/...); None for custom roles
    name: str
    description: str | None
    permissions: list[str]
    member_count: int = 0


class PermissionGroupOut(BaseModel):
    group: str
    codes: list[str]


class TeamOut(BaseModel):
    memberships: list[MembershipOut]
    roles: list[RoleOut]
    permission_groups: list[PermissionGroupOut]
    permission_labels: dict[str, str]
