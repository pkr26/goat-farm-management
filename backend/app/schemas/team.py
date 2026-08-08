"""Pydantic schemas for the team module (workers, roles, permissions)."""

from pydantic import BaseModel, ConfigDict, Field

from .auth import EmailMixin
from .common import BoundedId


class WorkerCreateIn(EmailMixin):
    email: str
    password: str | None = Field(default=None, max_length=128)  # required for a new account
    name: str | None = Field(default=None, max_length=120)
    role_id: BoundedId


class RoleChangeIn(BaseModel):
    role_id: BoundedId


class PasswordResetIn(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class MembershipOut(BaseModel):
    id: int
    user_id: int
    email: str
    name: str | None
    role_id: int | None
    role_name: str | None
    is_active: bool


class RoleIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=255)
    permissions: list[str] = []


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
