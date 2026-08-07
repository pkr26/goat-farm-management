"""Pydantic schemas for the auth endpoints."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _EmailMixin(BaseModel):
    @field_validator("email", check_fields=False)
    @classmethod
    def _email_ok(cls, value: str) -> str:
        email = value.strip().lower()
        if not email or "@" not in email:
            raise ValueError("Enter a valid email address")
        return email


class RegisterIn(_EmailMixin):
    email: str
    # max_length: no unbounded input into the (deliberately expensive) Argon2 hasher.
    password: str = Field(min_length=1, max_length=128)
    name: str | None = Field(default=None, max_length=120)  # users.name is String(120)


class LoginIn(_EmailMixin):
    email: str
    password: str = Field(max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    name: str | None


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class FarmOut(BaseModel):
    id: int
    name: str
    location: str | None
    role: str | None = None  # None = owner, else the membership's role name


class FarmCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    location: str | None = Field(default=None, max_length=120)  # farms.location is String(120)


class PermissionsOut(BaseModel):
    """Effective permission set for the current user on the X-Farm-Id farm."""

    is_owner: bool
    permissions: list[str]  # sorted permission codes; ALL of them when is_owner
