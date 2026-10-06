"""Constrained natural-language query-plan adapter tests."""

from unittest.mock import Mock

import pytest

from app.enterprise_ai.nl2sql import EnterpriseAINL2SQLClient, POC_NL2SQL_TABLES, QUERY_PLANS
from app.oci.database import READ_OPERATIONS


ALLOWED_TABLES = sorted(POC_NL2SQL_TABLES)


def client(database=None, *, enabled=True):
    return EnterpriseAINL2SQLClient(
        endpoint="https://inference.example.test",
        enabled=enabled,
        database_client=database,
    )


def test_disabled_nl2sql_returns_an_explicit_empty_result_without_database_access():
    database = Mock()

    result = client(database, enabled=False).query(
        dataset="inventory", allowed_tables=ALLOWED_TABLES,
    )

    assert result == {
        "status": "not_configured", "dataset": "inventory", "row_count": 0, "rows": [],
    }
    database.fetch_all.assert_not_called()


@pytest.mark.parametrize(
    ("dataset", "arguments", "operation", "parameters"),
    [
        (
            "inventory",
            {"item_id": "ITEM001", "sort_by": "stock_low_to_high", "limit": 5},
            "nl2sql_inventory",
            {"item_id": "ITEM001", "sort_by": "stock_low_to_high", "page_size": 5},
        ),
        (
            "sales_summary",
            {"days": 14, "sort_by": "sales_high_to_low", "limit": 10},
            "nl2sql_sales_summary",
            {"item_id": None, "days": 14, "sort_by": "sales_high_to_low", "page_size": 10},
        ),
        (
            "supplier_options",
            {"item_id": "ITEM001", "supplier_id": "SUP001",
             "sort_by": "price_low_to_high", "limit": 3},
            "nl2sql_supplier_options",
            {"item_id": "ITEM001", "supplier_id": "SUP001",
             "sort_by": "price_low_to_high", "page_size": 3},
        ),
    ],
)
def test_query_plans_use_only_fixed_parameterized_database_operations(
    dataset, arguments, operation, parameters,
):
    database = Mock()
    database.fetch_all.return_value = [{"item_id": "ITEM001"}]

    result = client(database).query(
        dataset=dataset, allowed_tables=ALLOWED_TABLES, **arguments,
    )

    assert result == {
        "status": "completed", "dataset": dataset, "row_count": 1,
        "rows": [{"item_id": "ITEM001"}],
    }
    database.fetch_all.assert_called_once_with(operation, parameters)
    definition = READ_OPERATIONS[QUERY_PLANS[dataset]["operation"]]
    assert definition.kind == "read" and definition.sql.lstrip().upper().startswith("SELECT")


@pytest.mark.parametrize(
    "arguments",
    [
        {"dataset": "cases"},
        {"dataset": "inventory", "sort_by": "price_low_to_high"},
        {"dataset": "inventory", "supplier_id": "SUP001"},
        {"dataset": "inventory", "item_id": "ITEM001' OR 1=1"},
        {"dataset": "inventory", "limit": 51},
    ],
)
def test_invalid_or_out_of_scope_query_plans_never_touch_the_database(arguments):
    database = Mock()

    with pytest.raises(ValueError):
        client(database).query(allowed_tables=ALLOWED_TABLES, **arguments)

    database.fetch_all.assert_not_called()


def test_disallowed_table_scope_is_rejected_before_database_access():
    database = Mock()

    with pytest.raises(ValueError, match="tables are not allowed"):
        client(database).query(dataset="inventory", allowed_tables=["items", "users"])

    database.fetch_all.assert_not_called()

