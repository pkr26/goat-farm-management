"""Pydantic schemas for the team module (workers, roles, permissions)."""

from pydantic import BaseModel, ConfigDict, Field

from .auth import MAX_EMAIL_LENGTH, EmailMixin, PasswordString
from .common import BoundedId, PostgresText, StrictBool, StrictInputModel, StrictInt


class WorkerCreateIn(EmailMixin):
    email: str = Field(max_length=MAX_EMAIL_LENGTH)
    # Required when creating a new account (owner-provisioned workers).
    password: PasswordString | None = Field(default=None, max_length=128)
    name: PostgresText | None = Field(default=None, max_length=120)
    role_id: BoundedId
    # Optional worker-tablet quick sign-in PIN (ITEM 2, 2026-09-21 playbook).
    # Digits only — the tablet renders a numeric pad. The deployment's
    # min length (4 dev / 6 production) is enforced by the handler so the
    # wire schema stays environment-independent.
    pin: str | None = Field(default=None, min_length=4, max_length=12, pattern=r"^[0-9]+$")


class WorkerPinResetIn(StrictInputModel):
    """Owner-managed rotation of one membership's tablet PIN."""

    pin: str = Field(min_length=4, max_length=12, pattern=r"^[0-9]+$")


class RoleChangeIn(StrictInputModel):
    role_id: BoundedId


class WorkerStatusIn(StrictInputModel):
    # StrictBool, like every other mutating boolean input: lax coercion would
    # let `1`/`"false"`/`"off"` silently flip a worker's access instead of
    # returning the 422 the strict-input contract promises.
    is_active: StrictBool


class PasswordResetIn(StrictInputModel):
    password: PasswordString = Field(min_length=1, max_length=128)


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
    # True when this membership can sign in on the worker tablet by PIN.
    pin_set: bool = False


class NotificationPrefsIn(StrictInputModel):
    """Owner-managed notification preferences for one membership."""

    phone: str = Field(min_length=10, max_length=20, pattern=r"^\+?[0-9]{10,19}$")
    daily_digest: bool = False
    screening_flags: bool = False
    kidding_watch: bool = False
    overdue_critical: bool = False
    feed_reorder: bool = False


class NotificationPrefsOut(BaseModel):
    membership_id: int
    phone: str
    daily_digest: bool
    screening_flags: bool
    kidding_watch: bool
    overdue_critical: bool
    feed_reorder: bool
    verified: bool


class RoleIn(StrictInputModel):
    name: PostgresText = Field(min_length=1, max_length=80)
    description: PostgresText | None = Field(default=None, max_length=255)
    permissions: list[str] = Field(default_factory=list, max_length=100)


class RoleUpdateIn(RoleIn):
    """Full replacement guarded against stale role-editor submissions."""

    # Compatibility bridge: all rows introduced by the migration start at 1,
    # so an older SPA can make one safe update. Its next stale/no-token save
    # then conflicts after the server increments the row to 2.
    expected_revision: StrictInt = Field(default=1, ge=1)


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str | None  # preset key (MOVER/VET/...); None for custom roles
    name: str
    description: str | None
    permissions: list[str]
    revision: int
    member_count: int = 0


class PermissionGroupOut(BaseModel):
    group: str
    codes: list[str]


class TeamOut(BaseModel):
    memberships: list[MembershipOut]
    roles: list[RoleOut]
    permission_groups: list[PermissionGroupOut]
    permission_labels: dict[str, str]
