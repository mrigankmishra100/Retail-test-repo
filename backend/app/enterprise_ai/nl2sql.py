"""Read-only adapter for Oracle Enterprise AI NL2SQL."""

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

POC_NL2SQL_TABLES = frozenset(
    {
        "items",
        "inventory",
        "sales_history",
        "suppliers",
        "supplier_items",
        "supplier_inventory",
    }
)

QUERY_PLANS = {
    "inventory": {
        "operation": "nl2sql_inventory",
        "tables": frozenset({"items", "inventory"}),
        "sorts": frozenset({"item_id", "stock_low_to_high", "stock_high_to_low"}),
    },
    "sales_summary": {
        "operation": "nl2sql_sales_summary",
        "tables": frozenset({"items", "sales_history"}),
        "sorts": frozenset({"item_id", "sales_high_to_low", "sales_low_to_high"}),
    },
    "supplier_options": {
        "operation": "nl2sql_supplier_options",
        "tables": frozenset({"suppliers", "supplier_items", "supplier_inventory"}),
        "sorts": frozenset(
            {"item_id", "price_low_to_high", "lead_time_low_to_high", "availability_high_to_low"}
        ),
    },
}


class EnterpriseAINL2SQLClient:
    """Translate questions into constrained, SELECT-only business-data queries.

    The production adapter must restrict tables, cap result sizes, enforce a
    timeout, and log generated SQL. It must reject INSERT, UPDATE, DELETE, DDL,
    commercial commitments, and case approval changes.
    """

    def __init__(self, endpoint: str | None = None, enabled: bool = False,
                 database_client: Any | None = None) -> None:
        self.endpoint = endpoint
        self.enabled = enabled
        self.database_client = database_client

    def query(self, *, dataset: str, allowed_tables: list[str], item_id: str | None = None,
              supplier_id: str | None = None, days: int = 30, sort_by: str = "item_id",
              limit: int = 20) -> dict[str, Any]:
        """Execute a model-selected, constrained query plan using fixed SQL."""
        normalized_tables = [table.lower() for table in allowed_tables]
        disallowed = sorted(set(normalized_tables) - POC_NL2SQL_TABLES)
        if disallowed:
            raise ValueError(f"NL2SQL tables are not allowed: {', '.join(disallowed)}")
        if dataset not in QUERY_PLANS:
            raise ValueError("NL2SQL dataset is not allowed")
        plan = QUERY_PLANS[dataset]
        if not plan["tables"].issubset(normalized_tables):
            raise ValueError("NL2SQL dataset requires a table outside the request scope")
        if sort_by not in plan["sorts"]:
            raise ValueError(f"Sort {sort_by} is not allowed for {dataset}")
        if item_id is not None and not re.fullmatch(r"ITEM[0-9]{3}", item_id):
            raise ValueError("Invalid item identifier")
        if supplier_id is not None and not re.fullmatch(r"SUP[0-9]{3}", supplier_id):
            raise ValueError("Invalid supplier identifier")
        if supplier_id is not None and dataset != "supplier_options":
            raise ValueError("Supplier filtering is available only for supplier options")
        if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 365:
            raise ValueError("NL2SQL days must be between 1 and 365")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
            raise ValueError("NL2SQL limit must be between 1 and 50")
        if not self.enabled or not self.endpoint or self.database_client is None:
            logger.info("Oracle Enterprise AI NL2SQL is not configured; returning no rows")
            return {
                "status": "not_configured",
                "dataset": dataset,
                "row_count": 0,
                "rows": [],
            }
        parameters = {"item_id": item_id, "sort_by": sort_by, "page_size": limit}
        if dataset == "sales_summary":
            parameters["days"] = days
        elif dataset == "supplier_options":
            parameters["supplier_id"] = supplier_id
        rows = self.database_client.fetch_all(plan["operation"], parameters)
        logger.info("Constrained NL2SQL dataset=%s rows=%d", dataset, len(rows))
        return {"status": "completed", "dataset": dataset, "row_count": len(rows), "rows": rows}
