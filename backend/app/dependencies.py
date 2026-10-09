"""Lazy application dependency container.

Object construction is centralized here so FastAPI, MCP tools, and future
graphs use the same configured boundaries. Constructing the container or any
of its properties must not initiate network connections.
"""

from functools import cached_property, lru_cache
import logging
import os

from app.config import REPOSITORY_ROOT, Settings, settings
from app.enterprise_ai import (
    EnterpriseAIConversationClient,
    EnterpriseAIGuardrailsClient,
    EnterpriseAIMCPSafetyClient,
    EnterpriseAINL2SQLClient,
    EnterpriseAIRegistryClient,
    EnterpriseAIResponsesClient,
)
from app.observability import LangSmithObservabilityClient
from app.oci import (
    OCICacheClient,
    OCINotificationClient,
    OCIObservabilityClient,
    OCIStorageClient,
    OracleDatabaseClient,
)
from app.services.inventory import InventoryService
from app.services.memory import MemoryService
from app.services.policy import PolicyService
from app.services.replenishment import ReplenishmentService
from app.services.supplier import SupplierService
from app.services.sessions import SessionService
from app.services.cases import CaseService
from app.services.chat import ChatService
from app.services.chat_history import ChatHistoryService
from app.services.readiness import ReadinessService
from app.oci.wallet import ObjectStorageWallet


class ApplicationContainer:
    """Create configured adapters and services lazily and share them per process."""

    def __init__(self, application_settings: Settings) -> None:
        self.settings = application_settings
        logging.getLogger(__name__).info('application_features_configured', extra={
            'service': application_settings.application_role,
            'release_tag': os.getenv('APP_RELEASE_TAG', 'development'),
            'features': {name: getattr(application_settings, name) for name in (
                'enterprise_ai_responses_enabled', 'enterprise_ai_api_mode',
                'enterprise_ai_conversation_state_enabled', 'enterprise_ai_file_search_enabled',
                'enterprise_ai_nl2sql_enabled', 'remote_mcp_enabled',
                'oci_notification_publish_enabled')},
            'status': 'configured_not_probed'})

    @cached_property
    def database(self) -> OracleDatabaseClient:
        password = self.settings.oracle_db_password
        wallet_password = self.settings.oracle_db_wallet_password
        runtime_wallet = None
        if self.settings.oracle_db_wallet_object_name:
            runtime_wallet = ObjectStorageWallet(
                namespace=self.settings.oracle_db_wallet_namespace,
                bucket=self.settings.oracle_db_wallet_bucket,
                object_name=self.settings.oracle_db_wallet_object_name,
                driver_mode=self.settings.oracle_db_driver_mode,
                auth_mode=self.settings.oci_auth_mode, region=self.settings.oci_region,
                config_file=self.settings.oci_config_file, config_profile=self.settings.oci_config_profile)
        return OracleDatabaseClient(
            dsn=self.settings.oracle_db_dsn,
            driver_mode=self.settings.oracle_db_driver_mode,
            user=self.settings.oracle_db_user,
            password=password.get_secret_value() if password is not None else None,
            config_dir=self.settings.oracle_db_config_dir,
            wallet_password=(
                wallet_password.get_secret_value() if wallet_password is not None else None
            ),
            pool_min=self.settings.oracle_db_pool_min,
            pool_max=self.settings.oracle_db_pool_max,
            pool_increment=self.settings.oracle_db_pool_increment,
            retry_count=self.settings.oracle_db_retry_count,
            retry_delay=self.settings.oracle_db_retry_delay,
            tcp_connect_timeout=self.settings.oracle_db_tcp_connect_timeout,
            runtime_wallet=runtime_wallet,
        )

    def close(self) -> None:
        """Close only resources that were created during this process."""
        if "readiness_service" in self.__dict__:
            self.readiness_service.close()
        if "database" in self.__dict__:
            self.database.close()
        if "storage" in self.__dict__:
            self.storage.close()
        if "cache" in self.__dict__:
            self.cache.close()
        if "notifications" in self.__dict__:
            self.notifications.close()

    @cached_property
    def cache(self) -> OCICacheClient:
        return OCICacheClient(endpoint=self.settings.oci_cache_endpoint,
                              ttl_seconds=self.settings.cache_state_ttl_seconds,
                              allow_memory=self.settings.app_env == "development",
                              username=self.settings.oci_cache_username,
                              password=self.settings.oci_cache_password.get_secret_value() if self.settings.oci_cache_password else None,
                              tls_ca_file=self.settings.oci_cache_tls_ca_file,
                              max_connections=self.settings.oci_cache_max_connections)

    @cached_property
    def session_service(self) -> SessionService:
        return SessionService(self.cache, self.database,
                              ttl_seconds=self.settings.session_ttl_seconds,
                              development=self.settings.app_env == "development")

    @cached_property
    def storage(self) -> OCIStorageClient:
        return OCIStorageClient(
            namespace=self.settings.oci_namespace,
            bucket_name=self.settings.oci_bucket_name,
            config_file=self.settings.oci_config_file,
            config_profile=self.settings.oci_config_profile,
            region=self.settings.oci_region,
            auth_mode=self.settings.oci_auth_mode,
        )

    @cached_property
    def notifications(self) -> OCINotificationClient:
        return OCINotificationClient(topic_id=self.settings.oci_notification_topic_id,
            enabled=self.settings.oci_notification_publish_enabled, config_file=self.settings.oci_config_file,
            config_profile=self.settings.oci_config_profile, supplier_topics=self.settings.oci_notification_supplier_topics,
            shared_poc_enabled=self.settings.oci_notification_shared_poc_enabled,
            auth_mode=self.settings.oci_auth_mode)

    @cached_property
    def oci_observability(self) -> OCIObservabilityClient:
        return OCIObservabilityClient()

    @cached_property
    def responses(self) -> EnterpriseAIResponsesClient:
        return EnterpriseAIResponsesClient.from_settings(self.settings)

    @cached_property
    def conversation(self) -> EnterpriseAIConversationClient:
        return EnterpriseAIConversationClient(
            endpoint=self.settings.enterprise_ai_endpoint,
            enabled=self.settings.enterprise_ai_conversation_state_enabled,
            responses=self.responses,
        )

    @cached_property
    def guardrails(self) -> EnterpriseAIGuardrailsClient:
        return EnterpriseAIGuardrailsClient(
            endpoint=self.settings.enterprise_ai_endpoint,
            enabled=self.settings.enterprise_ai_guardrails_enabled,
        )

    @cached_property
    def mcp_safety(self) -> EnterpriseAIMCPSafetyClient:
        return EnterpriseAIMCPSafetyClient(
            endpoint=self.settings.enterprise_ai_endpoint,
            enabled=self.settings.enterprise_ai_mcp_safety_enabled,
        )

    @cached_property
    def nl2sql(self) -> EnterpriseAINL2SQLClient:
        return EnterpriseAINL2SQLClient(
            endpoint=self.settings.enterprise_ai_endpoint,
            enabled=self.settings.enterprise_ai_nl2sql_enabled,
            database_client=self.database,
        )

    @cached_property
    def registry(self) -> EnterpriseAIRegistryClient:
        return EnterpriseAIRegistryClient.from_settings(self.settings)

    @cached_property
    def agent_registry_service(self):
        from app.services.agent_registry import AgentRegistryService
        return AgentRegistryService(self.database)

    @cached_property
    def langsmith(self) -> LangSmithObservabilityClient:
        return LangSmithObservabilityClient.from_settings(self.settings)

    @cached_property
    def inventory_service(self) -> InventoryService:
        return InventoryService(
            database_client=self.database,
            nl2sql_client=self.nl2sql,
            demand_window_days=self.settings.inventory_demand_window_days,
            risk_horizon_days=self.settings.inventory_risk_horizon_days,
            target_coverage_days=self.settings.inventory_target_coverage_days,
        )

    @cached_property
    def supplier_service(self) -> SupplierService:
        return SupplierService(database_client=self.database, policy_service=self.policy_service,
                               inventory_service=self.inventory_service)

    @cached_property
    def case_service(self) -> CaseService:
        from app.services.email_drafts import EmailDraftService
        return CaseService(self.database, self.supplier_service, self.notifications, self.mcp_safety,
                           email_drafts=EmailDraftService(self.database,
                               responses=self.responses,
                               shared_poc_enabled=self.settings.oci_notification_shared_poc_enabled))

    @cached_property
    def retail_workflow_service(self):
        if 'retail' in self.settings.agent_endpoints:
            from app.agents.remote import RemoteWorkflowService
            return RemoteWorkflowService(self.remote_retail_agent)
        from app.services.retail_workflow import RetailWorkflowService
        return RetailWorkflowService(self)

    @cached_property
    def supplier_workflow_service(self):
        if 'supplier' in self.settings.agent_endpoints:
            from app.agents.remote import RemoteWorkflowService
            return RemoteWorkflowService(self.remote_supplier_agent)
        from app.services.supplier_workflow import SupplierWorkflowService
        return SupplierWorkflowService(self)

    @cached_property
    def retail_conversation_service(self):
        if 'retail' in self.settings.agent_endpoints:
            from app.agents.remote import RemoteConversationService
            return RemoteConversationService(self.remote_retail_agent)
        from app.services.retail_conversation import RetailConversationService
        return RetailConversationService(self)

    def _remote_agent(self, name):
        from app.agents.remote import RemoteAgentClient
        return RemoteAgentClient(audience=name, endpoint=self.settings.agent_endpoints[name],
            key=self.settings.agent_service_keys[name], timeout=self.settings.agent_timeout_seconds,
            endpoint_resolver=lambda: self.agent_registry_service.active_endpoint(name)
                or self.settings.agent_endpoints[name])

    @cached_property
    def remote_retail_agent(self):
        return self._remote_agent('retail')

    @cached_property
    def remote_supplier_agent(self):
        return self._remote_agent('supplier')

    @cached_property
    def chat_service(self) -> ChatService:
        return ChatService(self.inventory_service, self.supplier_service, self.case_service)

    @cached_property
    def chat_history_service(self) -> ChatHistoryService:
        return ChatHistoryService(self.database)

    @cached_property
    def readiness_service(self) -> ReadinessService:
        # A slow probe must not borrow/close the API's active pools on shutdown.
        return ReadinessService(ApplicationContainer(self.settings))

    @cached_property
    def policy_service(self) -> PolicyService:
        directory = self.settings.policy_local_dir.expanduser()
        if not directory.is_absolute():
            directory = REPOSITORY_ROOT / directory
        return PolicyService(storage_client=self.storage, local_directory=directory)

    @cached_property
    def replenishment_service(self) -> ReplenishmentService:
        return ReplenishmentService(
            database_client=self.database,
            notification_client=self.notifications,
            mcp_safety_client=self.mcp_safety,
            case_service=self.case_service,
        )

    @cached_property
    def memory_service(self) -> MemoryService:
        return MemoryService(database_client=self.database, cache_client=self.cache, case_service=self.case_service)


@lru_cache(maxsize=1)
def get_container() -> ApplicationContainer:
    """Return the process-wide configured dependency container."""
    return ApplicationContainer(settings)
