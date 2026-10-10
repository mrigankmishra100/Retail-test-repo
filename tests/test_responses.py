"""Tests for offline and SDK-mapped OCI Generative AI response behavior."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.enterprise_ai.responses import EnterpriseAIResponsesClient


class FakeModel:
    """Small mutable stand-in for OCI SDK model classes."""

    def __init__(self, **values: object) -> None:
        vars(self).update(values)


class FakeGenericChatRequest(FakeModel):
    pass


def test_unconfigured_response_client_does_not_import_or_call_oci() -> None:
    result = asyncio.run(
        EnterpriseAIResponsesClient().create_response([{"role": "user", "content": "Hello"}])
    )
    assert result["status"] == "not_configured"
    assert result["output"] is None


def test_response_client_can_be_created_from_application_settings() -> None:
    settings = FakeModel(
        enterprise_ai_endpoint="https://inference.example.com",
        enterprise_ai_model_id="model-id",
        enterprise_ai_responses_enabled=True,
        oci_compartment_id="compartment-id",
        oci_config_file="config-file",
        oci_config_profile="POC",
    )
    client = EnterpriseAIResponsesClient.from_settings(settings)
    assert client.endpoint == settings.enterprise_ai_endpoint
    assert client.model_id == settings.enterprise_ai_model_id
    assert client.compartment_id == settings.oci_compartment_id
    assert client.config_profile == "POC"


def test_configured_client_uses_oci_responses_not_native_chat() -> None:
    response = FakeModel(
        data=FakeModel(
            chat_response=FakeModel(
                choices=[FakeModel(message=FakeModel(content=[FakeModel(text="OCI response")]))]
            )
        ),
        opc_request_id="request-1",
    )
    chat = Mock(return_value=response)
    inference_client = Mock(return_value=FakeModel(chat=chat))
    from_file = Mock(return_value={"region": "test-region"})

    models = FakeModel(
        ChatDetails=FakeModel,
        TextContent=FakeModel,
        Message=FakeModel,
        GenericChatRequest=FakeGenericChatRequest,
        BaseChatRequest=FakeModel(API_FORMAT_GENERIC="GENERIC"),
        OnDemandServingMode=FakeModel,
    )
    fake_oci = FakeModel(
        config=FakeModel(from_file=from_file),
        retry=FakeModel(NoneRetryStrategy=Mock(return_value="no-retry")),
        generative_ai_inference=FakeModel(
            GenerativeAiInferenceClient=inference_client,
            models=models,
        ),
    )
    client = EnterpriseAIResponsesClient(
        endpoint="https://inference.generativeai.ap-hyderabad-1.oci.oraclecloud.com/openai/v1",
        model_id="google.gemini-2.5-flash",
        project_id="ocid1.generativeaiproject.oc1.ap-hyderabad-1.example",
        config_file="~/.oci/config",
        config_profile="DEFAULT",
        enabled=True,
    )
    transport = Mock()
    transport.request.return_value = {'status': 'completed', 'output': [
        {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'OCI response'}]}]}
    client.__dict__['transport'] = transport

    with patch.dict("sys.modules", {"oci": fake_oci}):
        result = asyncio.run(client.create_response([{"role": "user", "content": "Hello"}]))

    assert result["status"] == "completed"
    assert result["output"] == "OCI response"
    chat.assert_not_called()
    assert transport.request.call_args.args[:2] == ('POST', '/responses')
    payload = transport.request.call_args.args[2]
    assert payload['input'] == [{'role': 'user', 'content': 'Hello'}]
    assert payload['max_output_tokens'] == 6000
