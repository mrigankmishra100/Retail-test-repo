"""Inventory, sales-history, and stock-risk service boundary."""

import math
from datetime import date, datetime
from typing import Any

from app.enterprise_ai.nl2sql import EnterpriseAINL2SQLClient
from app.oci.database import OracleDatabaseClient
from app.utils.calculations import (
    average_daily_demand,
    days_of_supply,
    projected_stock,
    recommended_replenishment_quantity,
    target_stock,
)


class ItemNotFound(LookupError):
    pass


class InventoryService:
    """Coordinate inventory lookup, sales history, and stock-risk analysis."""

    def __init__(
        self,
        database_client: OracleDatabaseClient | None = None,
        nl2sql_client: EnterpriseAINL2SQLClient | None = None,
        *,
        demand_window_days: int = 14,
        risk_horizon_days: int = 7,
        target_coverage_days: int = 14,
    ) -> None:
        for name, value in (
            ("demand_window_days", demand_window_days),
            ("risk_horizon_days", risk_horizon_days),
            ("target_coverage_days", target_coverage_days),
        ):
            if value < 1:
                raise ValueError(f"{name} must be at least 1")
        self.database_client = database_client or OracleDatabaseClient()
        self.nl2sql_client = nl2sql_client or EnterpriseAINL2SQLClient()
        self.demand_window_days = demand_window_days
        self.risk_horizon_days = risk_horizon_days
        self.target_coverage_days = target_coverage_days

    def get_inventory_risk(self, days: int | None = None) -> list[dict[str, Any]]:
        """Return only assessed items that are currently at risk."""
        return self.get_inventory_risk_report(days)["at_risk_items"]

    def get_inventory_risk_report(self, days: int | None = None) -> dict[str, Any]:
        """Calculate a reproducible risk report from inventory and sales rows."""
        horizon_days = self.risk_horizon_days if days is None else days
        if horizon_days < 1:
            raise ValueError("risk_horizon_days must be at least 1")

        at_risk_items: list[dict[str, Any]] = []
        not_at_risk_items: list[dict[str, Any]] = []
        unassessed_items: list[dict[str, Any]] = []
        inventory_rows = self.database_client.fetch_all("inventory_overview")

        for item in inventory_rows:
            item_id = str(item["item_id"])
            sales_rows = self.get_sales_history(item_id, self.demand_window_days)
            if not sales_rows:
                unassessed_items.append(
                    {
                        "item_id": item_id,
                        "item_name": str(item["item_name"]),
                        "current_stock": int(item["current_quantity"]),
                        "status": "missing_history",
                        "reason": (
                            f"No sales records were found in the configured "
                            f"{self.demand_window_days}-day demand window; demand is unknown, not zero."
                        ),
                    }
                )
                continue

            sale_days = {self._calendar_date(row["sale_date"]) for row in sales_rows}
            observed_days = len(sale_days)
            as_of_date = self._calendar_date(sales_rows[0]["as_of_date"])
            last_sale_date = max(sale_days)
            missing_days = self.demand_window_days - observed_days
            stale = (as_of_date - last_sale_date).days > 1
            warnings = []
            if missing_days:
                warnings.append(
                    f"Sales recorded on {observed_days} of {self.demand_window_days} calendar days. "
                    f"The {missing_days} missing days are excluded from the demand denominator; "
                    "the replenishment estimate is provisional."
                )
            if stale:
                warnings.append(
                    f"Sales history is stale: latest sale date is {last_sale_date.isoformat()} "
                    f"as of {as_of_date.isoformat()}; review demand before using this estimate."
                )

            total_demand = sum(float(row["quantity_sold"]) for row in sales_rows)
            average_demand = average_daily_demand(total_demand, observed_days)
            current_stock = float(item["current_quantity"])
            safety_stock = float(item["safety_stock"])
            projected = projected_stock(current_stock, average_demand, horizon_days)
            desired_stock = target_stock(
                average_demand,
                self.target_coverage_days,
                safety_stock,
            )
            recommended_quantity = recommended_replenishment_quantity(
                current_stock,
                average_demand,
                self.target_coverage_days,
                safety_stock,
            )
            is_at_risk = projected <= safety_stock
            supply_days = days_of_supply(current_stock, average_demand)
            assessment = {
                "item_id": item_id,
                "item_name": str(item["item_name"]),
                "category": str(item["category"]),
                "current_stock": int(current_stock),
                "reorder_point": int(item["reorder_point"]),
                "safety_stock": int(safety_stock),
                "demand_window_days": self.demand_window_days,
                "observed_days": observed_days,
                "history_quality": {
                    "as_of_date": as_of_date,
                    "last_sale_date": last_sale_date,
                    "missing_days": missing_days,
                    "is_stale": stale,
                    "recommendation_is_provisional": bool(warnings),
                    "warnings": warnings,
                },
                "total_demand": total_demand,
                "average_daily_demand": average_demand,
                "risk_horizon_days": horizon_days,
                "horizon_demand": average_demand * horizon_days,
                "projected_stock": projected,
                "days_of_supply": None if math.isinf(supply_days) else supply_days,
                "target_coverage_days": self.target_coverage_days,
                "target_stock": desired_stock,
                "recommended_quantity": recommended_quantity,
                "status": "at_risk" if is_at_risk else "not_at_risk",
                "explanation": self._build_explanation(
                    total_demand=total_demand,
                    observed_days=observed_days,
                    average_demand=average_demand,
                    current_stock=current_stock,
                    horizon_days=horizon_days,
                    projected=projected,
                    safety_stock=safety_stock,
                    desired_stock=desired_stock,
                    recommended_quantity=recommended_quantity,
                    is_at_risk=is_at_risk,
                ) + (" " + " ".join(warnings) if warnings else ""),
            }
            (at_risk_items if is_at_risk else not_at_risk_items).append(assessment)

        at_risk_items.sort(
            key=lambda item: (
                item["projected_stock"],
                item["days_of_supply"] if item["days_of_supply"] is not None else math.inf,
                item["item_id"],
            )
        )
        not_at_risk_items.sort(key=lambda item: item["item_id"])
        unassessed_items.sort(key=lambda item: item["item_id"])

        return {
            "rules": {
                "demand_window_days": self.demand_window_days,
                "risk_horizon_days": horizon_days,
                "target_coverage_days": self.target_coverage_days,
            },
            "summary": {
                "inventory_item_count": len(inventory_rows),
                "at_risk_count": len(at_risk_items),
                "not_at_risk_count": len(not_at_risk_items),
                "missing_history_count": len(unassessed_items),
            },
            "at_risk_items": at_risk_items,
            "not_at_risk_items": not_at_risk_items,
            "unassessed_items": unassessed_items,
        }

    def get_sales_history(self, item_id: str, days: int = 30) -> list[dict[str, Any]]:
        """Return real database sales history for one item and window."""
        if days < 1:
            raise ValueError("days must be at least 1")
        return self.database_client.fetch_all("item_sales_history", {"item_id": item_id, "days": days})

    def get_sales_page(self, item_id: str, days: int, *, limit: int, offset: int) -> dict:
        if not 1 <= days <= 365 or not 1 <= limit <= 100 or not 0 <= offset <= 10000:
            raise ValueError("Invalid sales window or pagination")
        if not self.database_client.fetch_all("item_details", {"item_id": item_id}):
            raise ItemNotFound("Item not found")
        rows = self.database_client.fetch_all("sales_page", {"item_id": item_id, "days": days,
                                                             "offset": offset, "page_size": limit + 1})
        # Existing Oracle data uses textual IDs; fresh schema can use numeric IDs.
        # Expose both consistently as opaque strings, never reinterpret them as quantities.
        rows = [{**row, "sale_id": str(row["sale_id"])} for row in rows]
        return dict(item_id=item_id, days=days, items=rows[:limit], limit=limit, offset=offset, has_more=len(rows)>limit)

    @staticmethod
    def _calendar_date(value: date | datetime) -> date:
        """Normalize Oracle DATE timestamps before counting observed days."""
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        raise ValueError("Sales history must contain valid calendar dates")

    def _build_explanation(
        self,
        *,
        total_demand: float,
        observed_days: int,
        average_demand: float,
        current_stock: float,
        horizon_days: int,
        projected: float,
        safety_stock: float,
        desired_stock: float,
        recommended_quantity: int,
        is_at_risk: bool,
    ) -> str:
        comparison = "<=" if is_at_risk else ">"
        outcome = "at risk" if is_at_risk else "not at risk"
        return (
            f"Average daily demand = {total_demand:g} / {observed_days} = {average_demand:g}. "
            f"Projected stock = {current_stock:g} - ({average_demand:g} x {horizon_days}) "
            f"= {projected:g}. Projected stock {projected:g} {comparison} safety stock "
            f"{safety_stock:g}, so the item is {outcome}. Target stock = "
            f"({average_demand:g} x {self.target_coverage_days}) + {safety_stock:g} = "
            f"{desired_stock:g}. Recommended quantity = ceil(max(0, {desired_stock:g} - "
            f"{current_stock:g})) = {recommended_quantity}."
        )

    def query_retail_data(self, *, dataset: str, item_id: str | None = None,
                          supplier_id: str | None = None, days: int = 30,
                          sort_by: str = "item_id", limit: int = 20) -> dict[str, Any]:
        """Execute a constrained model-selected plan over approved retail tables."""
        return self.nl2sql_client.query(
            dataset=dataset, item_id=item_id, supplier_id=supplier_id, days=days,
            sort_by=sort_by, limit=limit,
            allowed_tables=[
                "items", "inventory", "sales_history", "suppliers",
                "supplier_items", "supplier_inventory",
            ],
        )
