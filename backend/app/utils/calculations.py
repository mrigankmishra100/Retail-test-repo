"""Deterministic inventory calculations kept outside language-model reasoning."""

import math


def _require_non_negative(value: float, name: str) -> float:
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite, non-negative number")
    return value


def average_daily_demand(units_sold: float, number_of_days: float) -> float:
    """Calculate mean units sold per day; return zero for a zero-day window."""
    units = _require_non_negative(float(units_sold), "units_sold")
    days = _require_non_negative(float(number_of_days), "number_of_days")
    return 0.0 if days == 0 else units / days


def days_of_supply(available_quantity: float, average_daily_demand: float) -> float:
    """Calculate supply duration; return infinity when daily demand is zero."""
    available = _require_non_negative(float(available_quantity), "available_quantity")
    demand = _require_non_negative(float(average_daily_demand), "average_daily_demand")
    return math.inf if demand == 0 else available / demand


def lead_time_demand(average_daily_demand: float, lead_time_days: float) -> float:
    """Calculate expected demand over a supplier's lead time."""
    demand = _require_non_negative(float(average_daily_demand), "average_daily_demand")
    lead_time = _require_non_negative(float(lead_time_days), "lead_time_days")
    return demand * lead_time


def projected_stock(
    current_stock: float,
    average_daily_demand: float,
    risk_horizon_days: float,
) -> float:
    """Project stock remaining after demand over the configured risk horizon."""
    current = _require_non_negative(float(current_stock), "current_stock")
    return current - lead_time_demand(average_daily_demand, risk_horizon_days)


def target_stock(
    average_daily_demand: float,
    target_coverage_days: float,
    safety_stock: float,
) -> float:
    """Calculate demand coverage plus safety stock."""
    safety = _require_non_negative(float(safety_stock), "safety_stock")
    return lead_time_demand(average_daily_demand, target_coverage_days) + safety


def recommended_replenishment_quantity(
    current_stock: float,
    average_daily_demand: float,
    target_coverage_days: float,
    safety_stock: float,
) -> int:
    """Return the whole-unit quantity needed to reach target stock."""
    current = _require_non_negative(float(current_stock), "current_stock")
    desired = target_stock(average_daily_demand, target_coverage_days, safety_stock)
    return math.ceil(max(0.0, desired - current))


def should_replenish(
    available_quantity: float,
    average_daily_demand: float,
    lead_time_days: float,
    safety_stock: float,
) -> bool:
    """Return true when stock is at or below lead-time demand plus safety stock."""
    available = _require_non_negative(float(available_quantity), "available_quantity")
    safety = _require_non_negative(float(safety_stock), "safety_stock")
    threshold = lead_time_demand(average_daily_demand, lead_time_days) + safety
    return available <= threshold
