"""Offline import and construction tests for external-service adapters."""

from app.enterprise_ai import (
    EnterpriseAIConversationClient,
    EnterpriseAIGuardrailsClient,
    EnterpriseAIMCPSafetyClient,
    EnterpriseAINL2SQLClient,
    EnterpriseAIRegistryClient,
    EnterpriseAIResponsesClient,
)
from app.oci import (
    OCICacheClient,
    OCINotificationClient,
    OCIObservabilityClient,
    OCIStorageClient,
    OracleDatabaseClient,
)


def test_enterprise_ai_adapters_are_importable_without_network_calls() -> None:
    responses_client = EnterpriseAIResponsesClient()
    assert responses_client.enabled is False
    assert responses_client.compartment_id is None
    assert EnterpriseAIConversationClient().enabled is False
    assert EnterpriseAINL2SQLClient().enabled is False
    assert EnterpriseAIGuardrailsClient().enabled is False
    assert EnterpriseAIMCPSafetyClient().enabled is False
    registry = EnterpriseAIRegistryClient()
    assert registry.get_agent("retail") is None
    assert registry.get_mcp_server() is None


def test_oci_adapters_are_importable_without_network_calls() -> None:
    assert OracleDatabaseClient().dsn is None
    assert OCICacheClient().endpoint is None
    assert OCIStorageClient().bucket_name is None
    assert OCINotificationClient().topic_id is None
    assert OCIObservabilityClient().enabled is False
