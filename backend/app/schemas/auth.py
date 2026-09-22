"""Pydantic schemas for the auth endpoints."""

import re
from datetime import datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

from .common import MAX_INT32_ID, PostgresText, StrictInputModel

MAX_EMAIL_LENGTH = 254
MAX_PASSWORD_BYTES = 128


def _password_bounded_bytes(value: str) -> str:
    """AUTH-4 (2026-09-16): ``max_length=128`` counts code points, but Argon2
    hashes UTF-8 bytes — 128 astral-plane code points would be 512 bytes.
    Keep the bounded-input promise in the unit Argon2 actually sees."""
    if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError("Password must be at most 128 bytes when UTF-8 encoded")
    return value


PasswordString = Annotated[str, AfterValidator(_password_bounded_bytes)]

# tzdata entries that resolve under zoneinfo but that ECMA-402 excludes from the
# named time zones every browser must support, so Intl.DateTimeFormat raises a
# RangeError on them. Verified against this repo's tzdata: these are the only
# names in zoneinfo.available_timezones() that a browser rejects for being
# placeholders rather than for tzdata skew.
NON_LOCATION_TIMEZONES = frozenset({"Factory", "localtime"})

_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


class EmailMixin(StrictInputModel):
    """Shared bounded normalization and conservative mailbox validation.

    This intentionally accepts common RFC local-part punctuation (including
    apostrophes) while rejecting whitespace, control characters, malformed
    domains and attacker-sized values before they can become limiter keys or
    hit the VARCHAR(255) database column.
    """

    @field_validator("email", check_fields=False)
    @classmethod
    def _email_ok(cls, value: str) -> str:
        email = value.strip().lower()
        if len(email) > MAX_EMAIL_LENGTH or _EMAIL_RE.fullmatch(email) is None:
            raise ValueError("Enter a valid email address")
        return email


class RegisterIn(EmailMixin):
    email: str = Field(max_length=MAX_EMAIL_LENGTH)
    # max_length: no unbounded input into the (deliberately expensive) Argon2 hasher.
    password: PasswordString = Field(min_length=1, max_length=128)
    name: PostgresText | None = Field(default=None, max_length=120)  # users.name String(120)


class LoginIn(EmailMixin):
    email: str = Field(max_length=MAX_EMAIL_LENGTH)
    password: PasswordString = Field(max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    name: str | None
    # True while an owner-provisioned password awaits its holder's rotation.
    must_change_password: bool = False
    # TOTP second factor (2026-09-16): None = not enrolled, PENDING =
    # enrollment started but never confirmed, ACTIVE = demanded at login.
    totp_state: Literal["PENDING", "ACTIVE"] | None = None


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class LoginOut(BaseModel):
    """Login answers either a full session or, when TOTP is active, a
    short-lived single-use challenge token the client exchanges at
    /totp/challenge after prompting for the 6-digit code."""

    mfa_token: str | None = None
    access_token: str | None = None
    token_type: str = "bearer"
    user: UserOut | None = None


class TotpEnrollIn(StrictInputModel):
    current_password: PasswordString = Field(min_length=1, max_length=128)


class TotpCodeIn(StrictInputModel):
    code: str = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")


class TotpDisableIn(StrictInputModel):
    current_password: PasswordString = Field(min_length=1, max_length=128)
    code: str = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")


class TotpChallengeIn(StrictInputModel):
    mfa_token: str = Field(min_length=1, max_length=4096)
    # Either a 6-digit TOTP code or a single-use recovery code (XXXXX-XXXXX).
    # Case-insensitive input; the server canonicalizes before comparing.
    code: str = Field(
        min_length=6,
        max_length=11,
        pattern=r"^([0-9]{6}|[A-Za-z0-9]{5}-[A-Za-z0-9]{5})$",
    )


class TotpEnrollOut(BaseModel):
    secret: str
    otpauth_uri: str


class WorkerLoginIn(StrictInputModel):
    """Shared-tablet quick sign-in (ITEM 2, 2026-09-21 playbook)."""

    farm_id: int = Field(ge=1, le=MAX_INT32_ID)
    membership_id: int = Field(ge=1, le=MAX_INT32_ID)
    pin: str = Field(min_length=4, max_length=12, pattern=r"^[0-9]+$")


class WorkerRosterEntryOut(BaseModel):
    membership_id: int
    display_name: str


class WorkerRosterOut(BaseModel):
    """Who can tap-and-PIN on this farm's tablet.

    Deliberately unauthenticated: the tablet's first screen has no session.
    The accepted tradeoff (documented in README's worker-tablet section) is
    that display names of PIN-enabled workers are enumerable per farm id;
    names only — no emails, no roles, no counts of anything else. A worker
    provisioned without a name is listed as "Worker <membership_id>", never
    their email."""

    items: list[WorkerRosterEntryOut]


class TotpRecoveryCodesOut(BaseModel):
    """The one-time reveal of the account's recovery codes.

    Returned exactly once — at enrollment confirmation or regeneration — and
    never retrievable afterwards. The client owes the user a copy/print at
    reveal time; the server keeps only Argon2 hashes."""

    codes: list[str]


class TotpRecoveryRegenerateIn(StrictInputModel):
    """Re-mint the recovery-code set. Requires BOTH the current password and
    a currently-valid TOTP code: possession of an authenticated session alone
    must not be enough to rotate the break-glass material."""

    current_password: PasswordString = Field(min_length=1, max_length=128)
    code: str = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")


class AccountDeleteIn(StrictInputModel):
    current_password: PasswordString = Field(min_length=1, max_length=128)


class ChangePasswordIn(StrictInputModel):
    # Bound both values before they reach the deliberately expensive Argon2
    # verifier/hasher.
    current_password: PasswordString = Field(max_length=128)
    new_password: PasswordString = Field(min_length=1, max_length=128)


class AccountIdentityExport(BaseModel):
    id: int
    email: str
    name: str | None
    created_at: datetime


class OwnedFarmExport(BaseModel):
    id: int
    name: str
    location: str | None
    timezone: str
    created_at: datetime


class MembershipExport(BaseModel):
    farm_id: int
    farm_name: str
    role_id: int
    role_name: str
    is_active: bool
    created_at: datetime


class AccountExportOut(BaseModel):
    exported_at: datetime
    account: AccountIdentityExport
    owned_farms: list[OwnedFarmExport]
    memberships: list[MembershipExport]


class FarmOut(BaseModel):
    id: int
    name: str
    location: str | None
    # Both fields are always emitted by list/create. A nullable value is not an
    # optional wire key: role=None identifies ownership, while a missing role
    # would make the tenant selector ambiguous to generated clients.
    timezone: str
    role: str | None  # None = owner, else the membership's role name


class FarmCreateIn(StrictInputModel):
    name: PostgresText = Field(min_length=1, max_length=120)
    location: PostgresText | None = Field(
        default=None, max_length=120
    )  # farms.location is String(120)
    timezone: str = Field(default="Asia/Kolkata", min_length=1, max_length=64)

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        # tzdata ships a handful of non-location placeholder zones that ECMA-402
        # deliberately excludes, so a browser's Intl.DateTimeFormat throws on them
        # even though zoneinfo resolves them happily. They are never a real farm
        # location, so refuse them here rather than let one reach a client.
        # This is only the outer half of the guard: server and browser carry
        # independent tzdata, so a genuinely new zone (America/Coyhaique, say) can
        # still be unknown to an older browser. format.ts must therefore fall back
        # safely on its own — see todayInTimeZone/formatFarmDateTime.
        if value in NON_LOCATION_TIMEZONES:
            raise ValueError("timezone must be a real location, not a placeholder zone")
        return value


class PermissionsOut(BaseModel):
    """Effective permission set for the current user on the X-Farm-Id farm."""

    is_owner: bool
    permissions: list[str]  # sorted permission codes; ALL of them when is_owner
