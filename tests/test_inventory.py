"""Tests for deterministic inventory calculations."""

import math

from app.utils.calculations import average_daily_demand, days_of_supply, lead_time_demand, should_replenish


def test_average_daily_demand() -> None:
    assert average_daily_demand(140, 14) == 10
    assert average_daily_demand(10, 0) == 0


def test_days_of_supply() -> None:
    assert days_of_supply(100, 20) == 5
    assert math.isinf(days_of_supply(100, 0))


def test_should_replenish() -> None:
    assert should_replenish(100, 20, 3, 40) is True
    assert should_replenish(101, 20, 3, 40) is False


def test_lead_time_demand() -> None:
    assert lead_time_demand(20, 3) == 60
