"""Tests for configured Oracle Enterprise AI registry references."""

import pytest

from app.enterprise_ai.registry import EnterpriseAIRegistryClient


def test_registry_resolves_two_agents_and_one_mcp_server() -> None:
    registry = EnterpriseAIRegistryClient(
        agent_registry_enabled=True,
        mcp_registry_enabled=True,
        retail_agent_id="retail-agent-id",
        supplier_agent_id="supplier-agent-id",
        mcp_server_id="mcp-server-id",
    )

    resources = registry.configured_resources()

    assert resources["retail_agent"]["resource_id"] == "retail-agent-id"
    assert resources["supplier_agent"]["resource_id"] == "supplier-agent-id"
    assert resources["mcp_server"]["resource_id"] == "mcp-server-id"


def test_enabled_registry_fails_when_required_id_is_missing() -> None:
    registry = EnterpriseAIRegistryClient(agent_registry_enabled=True)

    with pytest.raises(ValueError, match="retail agent ID"):
        registry.get_agent("retail")
