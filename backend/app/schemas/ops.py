"""Typed contracts for unauthenticated liveness and readiness probes."""

from typing import Literal

from pydantic import BaseModel


class HealthStatusOut(BaseModel):
    status: Literal["ok"]


class ReadinessStatusOut(BaseModel):
    status: Literal["ready"]


class ReadinessUnavailableOut(BaseModel):
    status: Literal["unavailable"]
