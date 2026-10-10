"""Smoke tests for importable MCP tool functions."""

from app.mcp import tools


def test_mcp_tools_are_importable() -> None:
    assert callable(tools.get_inventory_risk)
    assert callable(tools.get_supplier_inventory)
    assert callable(tools.send_supplier_response)


def test_tools_reject_anonymous_calls_instead_of_returning_placeholders() -> None:
    import pytest
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="unauthorized"):
        tools.get_inventory_risk(days=7)


def test_fastmcp_server_initializes_without_oci_configuration() -> None:
    import pytest

    pytest.importorskip("fastmcp")
    from app.mcp.server import mcp

    assert mcp is not None
