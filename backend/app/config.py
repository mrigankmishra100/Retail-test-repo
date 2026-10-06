"""Environment-based application configuration."""

from pathlib import Path
import os
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.runtime_config import JsonSettingsMixin
from app.utils.logger import ensure_logging

ensure_logging()


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPOSITORY_ROOT / ".env"


class Settings(JsonSettingsMixin, BaseSettings):
    """Configuration loaded from environment variables or a local .env file."""

    app_env: str = "development"
    hosted_probe_mode: bool = False
    application_role: Literal['backend', 'retail-agent', 'supplier-agent'] = 'backend'
    agent_endpoints: dict[str, str] = Field(default_factory=dict)
    agent_service_keys: dict[str, SecretStr] = Field(default_factory=dict, repr=False)
    agent_service_key: SecretStr | None = Field(default=None, repr=False)
    agent_timeout_seconds: float = Field(default=250, gt=0, le=250)
    remote_mcp_enabled: bool = False
    remote_mcp_endpoint: str | None = None
    remote_mcp_service_keys: dict[str, SecretStr] = Field(default_factory=dict, repr=False)
    oci_auth_mode: Literal['api_key', 'resource_principal', 'instance_principal'] = 'api_key'
    frontend_url: str = "http://localhost:5173"

    enterprise_ai_endpoint: str | None = None
    enterprise_ai_model_id: str | None = None
    enterprise_ai_responses_enabled: bool = False
    enterprise_ai_api_mode: Literal['chat', 'responses'] = 'responses'
    enterprise_ai_auth_mode: Literal['oci', 'genai_api_key'] = 'oci'
    oci_genai_api_key: SecretStr | None = Field(default=None, repr=False)
    enterprise_ai_project_id: str | None = None
    enterprise_ai_timeout_seconds: float = Field(default=60, gt=0, le=120)
    enterprise_ai_max_tokens: int = Field(default=6000, ge=1, le=16000)
    enterprise_ai_temperature: float = Field(default=1.0, ge=0, le=2)
    enterprise_ai_top_p: float = Field(default=0.95, gt=0, le=1)
    enterprise_ai_top_k: int = Field(default=1, ge=-1)
    enterprise_ai_conversation_state_enabled: bool = False
    enterprise_ai_nl2sql_enabled: bool = False
    enterprise_ai_file_search_enabled: bool = False
    enterprise_ai_vector_store_ids: list[str] = Field(default_factory=list)
    enterprise_ai_guardrails_enabled: bool = False
    enterprise_ai_mcp_safety_enabled: bool = False
    enterprise_ai_agent_registry_enabled: bool = False
    enterprise_ai_mcp_registry_enabled: bool = False
    enterprise_ai_retail_agent_id: str | None = None
    enterprise_ai_supplier_agent_id: str | None = None
    enterprise_ai_mcp_server_id: str | None = None

    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "retail-inventory-agent-poc"
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_workspace_id: str | None = None

    oci_region: str | None = None
    oci_compartment_id: str | None = None
    oci_config_file: str = "~/.oci/config"
    oci_config_profile: str = "DEFAULT"
    oracle_db_dsn: str | None = None
    oracle_db_driver_mode: Literal['thin', 'thick'] = 'thin'
    oracle_db_user: str | None = None
    oracle_db_password: SecretStr | None = None
    oracle_db_config_dir: str | None = None
    oracle_db_wallet_password: SecretStr | None = None
    oracle_db_wallet_namespace: str | None = None
    oracle_db_wallet_bucket: str | None = None
    oracle_db_wallet_object_name: str | None = None
    oracle_db_pool_min: int = Field(default=1, ge=0)
    oracle_db_pool_max: int = Field(default=4, ge=1)
    oracle_db_pool_increment: int = Field(default=1, ge=1)
    oracle_db_retry_count: int = Field(default=0, ge=0)
    oracle_db_retry_delay: int = Field(default=1, ge=0)
    oracle_db_tcp_connect_timeout: float = Field(default=10.0, gt=0)
    inventory_demand_window_days: int = Field(default=14, ge=1)
    inventory_risk_horizon_days: int = Field(default=7, ge=1)
    inventory_target_coverage_days: int = Field(default=14, ge=1)
    oci_namespace: str | None = None
    oci_bucket_name: str | None = None
    policy_local_dir: Path = REPOSITORY_ROOT / "policies"
    oci_cache_endpoint: str | None = None
    oci_cache_username: str | None = None
    oci_cache_password: SecretStr | None = None
    oci_cache_tls_ca_file: str | None = None
    oci_cache_max_connections: int = Field(default=20, ge=1, le=100)
    oci_notification_publish_enabled: bool = False
    oci_notification_shared_poc_enabled: bool = False
    oci_notification_supplier_topics: dict[str, str] = Field(default_factory=dict)
    session_ttl_seconds: int = Field(default=3600, ge=60, le=86400)
    cache_state_ttl_seconds: int = Field(default=3600, ge=60, le=86400)
    oci_notification_topic_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OCI_NOTIFICATION_TOPIC_ID", "OCI_NOTIFICATION_OCID"),
    )

    model_config = SettingsConfigDict(env_file=Path(os.getenv('RETAIL_ENV_FILE', str(ENV_FILE))),
                                     extra="ignore", hide_input_in_errors=True)

    @model_validator(mode="after")
    def validate_enabled_integrations(self) -> "Settings":
        """Require configuration only for integrations explicitly enabled."""
        missing: list[str] = []
        from app.agents.remote_contract import validate_agent_endpoint, validate_service_key
        names = {'retail', 'supplier'}
        if set(self.remote_mcp_service_keys) - names:
            raise ValueError('MCP delegation supports only retail and supplier')
        for key in self.remote_mcp_service_keys.values():
            validate_service_key(key)
        if len(self.remote_mcp_service_keys) == 2 and self.remote_mcp_service_keys['retail'] == self.remote_mcp_service_keys['supplier']:
            raise ValueError('MCP delegation requires distinct role keys')
        if self.remote_mcp_enabled:
            validate_agent_endpoint(self.remote_mcp_endpoint or '')
            role = 'supplier' if self.application_role == 'supplier-agent' else 'retail'
            if role not in self.remote_mcp_service_keys:
                raise ValueError('Remote MCP requires the application role delegation key')
        if set(self.agent_endpoints) - names or set(self.agent_service_keys) - names:
            raise ValueError('Agent routing supports only retail and supplier')
        if set(self.agent_endpoints) != set(self.agent_service_keys):
            raise ValueError('Each agent endpoint requires its own service key')
        for name, endpoint in self.agent_endpoints.items():
            validate_agent_endpoint(endpoint)
            validate_service_key(self.agent_service_keys[name])
        if len(self.agent_service_keys) == 2 and self.agent_service_keys['retail'] == self.agent_service_keys['supplier']:
            raise ValueError('Retail and supplier agents require different service keys')
        if self.application_role != 'backend':
            if self.agent_endpoints or self.agent_service_keys:
                raise ValueError('Agent applications cannot forward to other agent applications')
            validate_service_key(self.agent_service_key)
        wallet_settings = (self.oracle_db_wallet_namespace, self.oracle_db_wallet_bucket,
                           self.oracle_db_wallet_object_name)
        if any(wallet_settings):
            if not all(value and value.strip() for value in wallet_settings):
                raise ValueError('Remote wallet requires ORACLE_DB_WALLET_NAMESPACE, ORACLE_DB_WALLET_BUCKET and ORACLE_DB_WALLET_OBJECT_NAME')
            if self.oracle_db_config_dir:
                raise ValueError('Remove ORACLE_DB_CONFIG_DIR when using a remote wallet; its directory is managed automatically')
        import re
        if any(not re.fullmatch(r'[A-Za-z0-9_.-]{1,256}', value) for value in self.enterprise_ai_vector_store_ids):
            raise ValueError('Invalid vector store identifier')
        if len(self.enterprise_ai_vector_store_ids) > 4:
            raise ValueError('At most four policy vector stores are supported')
        if self.enterprise_ai_file_search_enabled and (
                not self.enterprise_ai_responses_enabled or self.enterprise_ai_api_mode != 'responses'
                or not self.enterprise_ai_vector_store_ids):
            raise ValueError('File Search requires enabled Responses and configured policy vector stores')
        topics = self.oci_notification_supplier_topics
        if self.oci_notification_shared_poc_enabled:
            if self.app_env != "development":
                raise ValueError("Shared POC email is restricted to development")
            if not (self.oci_notification_topic_id or "").startswith("ocid1.onstopic."):
                raise ValueError("Shared POC email requires a notification topic OCID")
        if any(not re.fullmatch(r"SUP[0-9]{3}", supplier) or not topic.startswith("ocid1.onstopic.")
               for supplier, topic in topics.items()):
            raise ValueError("Supplier topic mappings require SUP identifiers and notification topic OCIDs")
        if len(set(topics.values())) != len(topics) or self.oci_notification_topic_id in topics.values():
            raise ValueError("Each supplier requires a distinct topic, separate from the shared notification topic")
        if self.oci_notification_publish_enabled:
            missing.extend(self._missing("Notifications", OCI_NOTIFICATION_TOPIC_ID=self.oci_notification_topic_id))

        if self.oracle_db_pool_max < self.oracle_db_pool_min:
            raise ValueError("ORACLE_DB_POOL_MAX must be greater than or equal to ORACLE_DB_POOL_MIN")

        if self.enterprise_ai_responses_enabled:
            if self.enterprise_ai_api_mode != 'responses':
                raise ValueError('Enabled AI requires OCI Responses API mode')
            if self.enterprise_ai_auth_mode == 'genai_api_key':
                if self.enterprise_ai_api_mode != 'responses':
                    raise ValueError('GenAI API-key authentication requires Responses API mode')
                missing.extend(self._missing('GenAI API-key authentication', OCI_GENAI_API_KEY=self.oci_genai_api_key))
                if self.oci_genai_api_key:
                    from app.enterprise_ai.responses_transport import validate_genai_key
                    validate_genai_key(self.oci_genai_api_key)
            missing.extend(
                self._missing(
                    "Enterprise AI Responses",
                    ENTERPRISE_AI_ENDPOINT=self.enterprise_ai_endpoint,
                    ENTERPRISE_AI_MODEL_ID=self.enterprise_ai_model_id,
                    **({'ENTERPRISE_AI_PROJECT_ID': self.enterprise_ai_project_id}
                       if self.enterprise_ai_api_mode == 'responses'
                       else {'OCI_COMPARTMENT_ID': self.oci_compartment_id}),
                )
            )

        if self.enterprise_ai_conversation_state_enabled:
            if not self.enterprise_ai_responses_enabled or self.enterprise_ai_api_mode != 'responses':
                raise ValueError('Conversation State requires enabled OCI Responses API mode')
        if self.enterprise_ai_responses_enabled and self.enterprise_ai_api_mode == 'responses' and not missing:
            from app.enterprise_ai.responses_transport import normalize_endpoint
            normalize_endpoint(self.enterprise_ai_endpoint, self.enterprise_ai_project_id)
            if (self.enterprise_ai_model_id or '').startswith('ocid1.generativeaimodel.'):
                raise ValueError('Responses requires a supported model name or endpoint OCID, not a native Chat model OCID')

        enabled_endpoint_features = {
            "Conversation State": self.enterprise_ai_conversation_state_enabled,
            "NL2SQL": self.enterprise_ai_nl2sql_enabled,
            "Guardrails": self.enterprise_ai_guardrails_enabled,
            "MCP Safety": self.enterprise_ai_mcp_safety_enabled,
        }
        for feature, enabled in enabled_endpoint_features.items():
            if enabled:
                missing.extend(self._missing(feature, ENTERPRISE_AI_ENDPOINT=self.enterprise_ai_endpoint))

        if self.enterprise_ai_agent_registry_enabled:
            missing.extend(
                self._missing(
                    "Agent Registry",
                    ENTERPRISE_AI_RETAIL_AGENT_ID=self.enterprise_ai_retail_agent_id,
                    ENTERPRISE_AI_SUPPLIER_AGENT_ID=self.enterprise_ai_supplier_agent_id,
                )
            )
        if self.enterprise_ai_mcp_registry_enabled:
            missing.extend(
                self._missing("MCP Registry", ENTERPRISE_AI_MCP_SERVER_ID=self.enterprise_ai_mcp_server_id)
            )
        if self.langsmith_tracing:
            missing.extend(self._missing("LangSmith tracing", LANGSMITH_API_KEY=self.langsmith_api_key))

        if missing:
            raise ValueError("Enabled integration configuration is incomplete: " + "; ".join(missing))
        return self

    @staticmethod
    def _missing(feature: str, **settings: str | SecretStr | None) -> list[str]:
        """Return missing-setting messages without exposing their values."""
        missing_names = [
            name
            for name, value in settings.items()
            if value is None or (isinstance(value, SecretStr) and not value.get_secret_value()) or value == ""
        ]
        return [f"{feature} requires {', '.join(missing_names)}"] if missing_names else []


settings = Settings()
