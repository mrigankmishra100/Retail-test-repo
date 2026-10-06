"""Configured references to Oracle Enterprise AI registry resources.

Registration and deletion are deployment operations and are intentionally not
performed by the application process. The runtime resolves the two hosted
agents and the shared hosted MCP server from IDs supplied by configuration.
"""

from dataclasses import asdict, dataclass
from typing import Any, Literal


AgentRole = Literal["retail", "supplier"]


@dataclass(frozen=True)
class RegistryResource:
    """A configured Oracle Enterprise AI registry resource."""

    resource_id: str
    resource_type: Literal["agent", "mcp_server"]
    logical_name: str

    def as_dict(self) -> dict[str, str]:
        """Return a serialization-safe representation for application state."""
        return asdict(self)


class EnterpriseAIRegistryClient:
    """Resolve agent and MCP registry entries without import-time OCI calls."""

    def __init__(
        self,
        *,
        agent_registry_enabled: bool = False,
        mcp_registry_enabled: bool = False,
        retail_agent_id: str | None = None,
        supplier_agent_id: str | None = None,
        mcp_server_id: str | None = None,
    ) -> None:
        self.agent_registry_enabled = agent_registry_enabled
        self.mcp_registry_enabled = mcp_registry_enabled
        self.retail_agent_id = retail_agent_id
        self.supplier_agent_id = supplier_agent_id
        self.mcp_server_id = mcp_server_id

    @classmethod
    def from_settings(cls, settings: Any) -> "EnterpriseAIRegistryClient":
        """Create registry references from environment-backed settings."""
        return cls(
            agent_registry_enabled=settings.enterprise_ai_agent_registry_enabled,
            mcp_registry_enabled=settings.enterprise_ai_mcp_registry_enabled,
            retail_agent_id=settings.enterprise_ai_retail_agent_id,
            supplier_agent_id=settings.enterprise_ai_supplier_agent_id,
            mcp_server_id=settings.enterprise_ai_mcp_server_id,
        )

    def get_agent(self, role: AgentRole) -> RegistryResource | None:
        """Resolve a configured hosted agent, or return ``None`` when disabled."""
        if not self.agent_registry_enabled:
            return None
        resource_id = self.retail_agent_id if role == "retail" else self.supplier_agent_id
        if not resource_id:
            raise ValueError(f"Agent Registry is enabled but the {role} agent ID is missing")
        return RegistryResource(resource_id, "agent", f"{role}_agent")

    def get_mcp_server(self) -> RegistryResource | None:
        """Resolve the shared hosted MCP server, or return ``None`` when disabled."""
        if not self.mcp_registry_enabled:
            return None
        if not self.mcp_server_id:
            raise ValueError("MCP Registry is enabled but the MCP server ID is missing")
        return RegistryResource(self.mcp_server_id, "mcp_server", "retail_inventory_mcp")

    def configured_resources(self) -> dict[str, dict[str, str] | None]:
        """Return all runtime registry references for diagnostics and wiring."""
        retail = self.get_agent("retail")
        supplier = self.get_agent("supplier")
        mcp_server = self.get_mcp_server()
        return {
            "retail_agent": retail.as_dict() if retail else None,
            "supplier_agent": supplier.as_dict() if supplier else None,
            "mcp_server": mcp_server.as_dict() if mcp_server else None,
        }
