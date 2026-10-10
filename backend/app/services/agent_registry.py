"""Small Oracle-backed directory; separate from the Enterprise AI registry adapter."""

from datetime import datetime
import logging
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agents.remote_contract import validate_agent_endpoint

logger = logging.getLogger(__name__)


class AgentRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    name: str = Field(min_length=1, max_length=120)
    agent_type: Literal["RETAIL", "SUPPLIER", "CUSTOM"]
    description: str = Field(default="", max_length=1000)
    endpoint_url: str = Field(min_length=1, max_length=2000)
    active: bool = Field(default=False, strict=True)

    @field_validator("name", "description")
    @classmethod
    def trim_text(cls, value):
        return value.strip()

    @field_validator("name")
    @classmethod
    def require_name(cls, value):
        if not value:
            raise ValueError("Agent name is required")
        return value

    @field_validator("endpoint_url")
    @classmethod
    def validate_url(cls, value):
        # urlsplit silently removes some control characters; reject them first.
        if "?" in value or "#" in value or any(
                character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Agent URL must not contain whitespace or control characters")
        return validate_agent_endpoint(value)


class RegisteredAgent(AgentRegistration):
    agent_id: UUID
    created_at: datetime
    updated_at: datetime


class AgentPage(BaseModel):
    items: list[RegisteredAgent]
    has_more: bool


class RegistryConflict(Exception):
    """Another agent of the same routed type is already active."""


class RegistryNotFound(Exception):
    """The requested registry entry does not exist."""


def oracle_error_code(exc):
    return getattr(exc.args[0], "code", None) if exc.args else None


class AgentRegistryService:
    def __init__(self, database):
        self.database = database

    @staticmethod
    def _agent(row):
        return RegisteredAgent.model_validate({
            **row, "active": row["active"] == 1, "description": row.get("description") or "",
        })

    def list_agents(self, *, limit=20, offset=0):
        rows = self.database.fetch_all("registry_agents", {"page_size": limit + 1, "offset": offset})
        return AgentPage(items=[self._agent(row) for row in rows[:limit]], has_more=len(rows) > limit)

    def _get(self, agent_id):
        rows = self.database.fetch_all("registry_agent", {"agent_id": str(agent_id)})
        if not rows:
            raise RegistryNotFound()
        return self._agent(rows[0])

    def _write(self, operation, parameters):
        try:
            return self.database.execute(operation, parameters)
        except Exception as exc:
            # The Oracle unique index enforces one active Retail/Supplier even
            # when different sessions register or activate agents concurrently.
            if oracle_error_code(exc) == 1:
                raise RegistryConflict() from exc
            raise

    def create(self, registration: AgentRegistration):
        agent_id = str(uuid4())
        self._write("registry_create_agent", {
            **registration.model_dump(), "agent_id": agent_id, "active": int(registration.active),
        })
        return self._get(agent_id)

    def set_active(self, agent_id, active):
        if not self._write("registry_set_active", {"agent_id": str(agent_id), "active": int(active)}):
            raise RegistryNotFound()
        return self._get(agent_id)

    def active_endpoint(self, audience):
        if audience not in {"retail", "supplier"}:
            raise ValueError("Only Retail and Supplier agents may be invoked")
        try:
            rows = self.database.fetch_all("registry_active_endpoint", {"agent_type": audience.upper()})
        except Exception as exc:
            if oracle_error_code(exc) == 942:
                # Additive rollout: an existing database may not be migrated yet.
                logger.info('agent_registry_route', extra={'agent': audience, 'source': 'configured_endpoint',
                    'reason': 'registry_migration_missing'})
                return None
            logger.warning('agent_registry_route_failed', extra={'agent': audience,
                'stage': 'endpoint_resolution', 'error_type': type(exc).__name__}, exc_info=True)
            raise
        if not rows:
            logger.info('agent_registry_route', extra={'agent': audience, 'source': 'configured_endpoint',
                'reason': 'no_active_entry'})
            return None
        if len(rows) != 1:
            raise RuntimeError("Agent registry has multiple active destinations")
        # Validate again at the outbound boundary, including DB-side edits.
        endpoint = AgentRegistration.validate_url(rows[0]["endpoint_url"])
        logger.info('agent_registry_route', extra={'agent': audience, 'source': 'registry', 'status': 'resolved'})
        return endpoint
