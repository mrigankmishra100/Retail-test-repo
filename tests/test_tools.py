"""Smoke tests for importable MCP tool functions."""

from app.mcp import tools


def test_mcp_tools_are_importable() -> None:
    assert callable(tools.get_inventory_risk)
    assert callable(tools.get_supplier_inventory)
    assert callable(tools.send_supplier_response)


def test_tools_reject_calls_without_a_transport_session(monkeypatch) -> None:
    import pytest
    from fastmcp.exceptions import ToolError
    from app.services.sessions import SessionError

    def missing_session():
        raise SessionError("Bearer session required")

    monkeypatch.setattr(tools, "get_session", missing_session)
    with pytest.raises(ToolError, match="unauthorized"):
        tools.get_inventory_risk(days=7)


def test_fastmcp_server_initializes_without_oci_configuration() -> None:
    import pytest

    pytest.importorskip("fastmcp")
    from app.mcp.server import mcp

    assert mcp is not None
