"""Smoke tests for importable MCP tool functions."""

from app.mcp import tools


def test_mcp_tools_are_importable() -> None:
    assert callable(tools.get_inventory_risk)
    assert callable(tools.get_supplier_inventory)
    assert callable(tools.send_supplier_response)


def test_tools_reject_unauthenticated_calls_before_service_access(monkeypatch) -> None:
    import pytest
    from unittest.mock import Mock
    from fastmcp.exceptions import ToolError
    from app.services.sessions import SessionError
    service = Mock()
    def missing_session():
        raise SessionError("Bearer session required")
    monkeypatch.setattr(tools, "get_session", missing_session)
    monkeypatch.setattr(tools, "tool_container", service)
    with pytest.raises(ToolError, match="unauthorized"):
        tools.get_inventory_risk(days=7)
    service.assert_not_called()


def test_fastmcp_server_initializes_without_oci_configuration() -> None:
    import pytest

    pytest.importorskip("fastmcp")
    from app.mcp.server import mcp

    assert mcp is not None
