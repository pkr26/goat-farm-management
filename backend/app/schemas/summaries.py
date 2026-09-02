"""Purpose-specific nested DTOs for aggregate and operational pages.

These views need enough animal identity to render cards and links, but they
must not accidentally inherit every financial, health, provenance, mortality,
and free-form field added to the full animal profile over time.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict

from .animals import AnimalStatusStr, BucketStr, Sex
from .tasks import TaskCategoryStr, TaskStatusStr


class AnimalIdentityOut(BaseModel):
    """Safe identity used when an aggregate page names an animal."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    tag_number: str
    name: str | None


class BucketAnimalOut(AnimalIdentityOut):
    """Operational fields consumed by the bucket-board table."""

    sex: Sex
    latest_weight_kg: float | None
    days_in_current_bucket: int


class DashboardWeightOut(BaseModel):
    """A recent weight with the animal identity needed to act on it."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    date: date
    weight_kg: float
    bcs: int | None
    animal: AnimalIdentityOut
    # Present as null when the caller lacks both animal and health access, so
    # the dashboard has one stable purpose-specific contract without leaking
    # the clinical free-form narrative.
    notes: str | None


class DashboardKiddingDueOut(BaseModel):
    """Minimum context required by the dashboard's kidding-due card."""

    id: int
    doe_id: int
    doe_tag: str
    expected_kidding_date: date | None


class PurchaseQuarantineAnimalOut(BaseModel):
    """Animal state needed to track a purchase through quarantine.

    Procurement access alone does not grant the animal register, so this DTO
    deliberately contains only the fields rendered by the batch detail table.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    tag_number: str
    sex: Sex
    current_bucket: BucketStr
    status: AnimalStatusStr


class QuarantineScheduleTaskOut(BaseModel):
    """Non-attributed quarantine schedule row for purchase workflows."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    due_date: date
    status: TaskStatusStr
    category: TaskCategoryStr
