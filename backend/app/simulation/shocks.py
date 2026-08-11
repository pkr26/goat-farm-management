"""Internal monthly shock paths shared by the core and Monte Carlo driver."""

from dataclasses import dataclass


@dataclass(frozen=True)
class MonthlyShockPath:
    meat_price: list[float]
    feed_price: list[float]
    fodder_yield: list[float]
    adult_mortality: list[float]
    kid_mortality: list[float]
    conception: list[float]
    litter_size: list[float]
    operating_cost: list[float]
    disease_outbreaks: int = 0
    drought_events: int = 0
    market_crashes: int = 0

    @classmethod
    def neutral(cls, horizon_months: int) -> "MonthlyShockPath":
        ones = [1.0] * horizon_months
        return cls(
            meat_price=ones.copy(),
            feed_price=ones.copy(),
            fodder_yield=ones.copy(),
            adult_mortality=ones.copy(),
            kid_mortality=ones.copy(),
            conception=ones.copy(),
            litter_size=ones.copy(),
            operating_cost=ones.copy(),
        )
