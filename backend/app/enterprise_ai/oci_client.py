"""Reusable client for OCI Generative AI Inference.

This module owns OCI SDK setup and the translation from the application's chat
message format to OCI's generic chat request. It performs no credential or
network work at import time.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


class OCIGenerativeAIClient:
    """Call an on-demand OCI Generative AI chat model."""

    def __init__(
        self,
        *,
        endpoint: str,
        model_id: str,
        compartment_id: str,
        config_file: str = "~/.oci/config",
        config_profile: str = "DEFAULT",
        max_tokens: int = 6000,
        temperature: float = 1.0,
        top_p: float = 0.95,
        top_k: int = 1,
        auth_mode: str = 'api_key',
    ) -> None:
        self.endpoint = endpoint
        self.model_id = model_id
        self.compartment_id = compartment_id
        self.config_file = config_file
        self.config_profile = config_profile
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.top_p = top_p
        self.top_k = top_k
        self.auth_mode = auth_mode

    def chat(self, messages: Sequence[Mapping[str, Any]], *,
             tools: Sequence[Mapping[str, Any]] | None = None) -> Any:
        """Send messages to the configured model and return the OCI response."""
        try:
            import oci
        except ImportError as exc:  # pragma: no cover - depends on installed extras
            raise RuntimeError("The 'oci' package is required for OCI Generative AI calls") from exc

        client_auth = {}
        if self.auth_mode == 'resource_principal':
            config = {}
            client_auth['signer'] = oci.auth.signers.get_resource_principals_signer()
        elif self.auth_mode == 'instance_principal':
            config = {}
            client_auth['signer'] = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
        elif self.auth_mode == 'api_key':
            config = oci.config.from_file(str(Path(self.config_file).expanduser()), self.config_profile)
        else:
            raise ValueError('Unsupported OCI authentication mode')
        client = oci.generative_ai_inference.GenerativeAiInferenceClient(
            config=config,
            service_endpoint=self.endpoint,
            retry_strategy=oci.retry.NoneRetryStrategy(),
            timeout=(10, 240),
            **client_auth,
        )
        models = oci.generative_ai_inference.models
        chat_request = models.GenericChatRequest()
        chat_request.api_format = models.BaseChatRequest.API_FORMAT_GENERIC
        chat_request.messages = [self._to_oci_message(models, message) for message in messages]
        chat_request.max_tokens = self.max_tokens
        chat_request.temperature = self.temperature
        chat_request.frequency_penalty = 0
        chat_request.presence_penalty = 0
        chat_request.top_p = self.top_p
        chat_request.top_k = self.top_k
        if tools:
            chat_request.tools = [models.FunctionDefinition(
                name=tool["name"], description=tool["description"], parameters=tool["parameters"]
            ) for tool in tools]
            chat_request.tool_choice = models.ToolChoiceAuto()
            chat_request.is_parallel_tool_calls = False

        chat_detail = models.ChatDetails()
        chat_detail.serving_mode = models.OnDemandServingMode(model_id=self.model_id)
        chat_detail.chat_request = chat_request
        chat_detail.compartment_id = self.compartment_id
        return client.chat(chat_detail)

    @staticmethod
    def _to_oci_message(models: Any, message: Mapping[str, Any]) -> Any:
        """Convert a project chat message into an OCI SDK message."""
        role = str(message.get("role", "USER")).upper().replace("-", "_")
        if role not in {"USER", "ASSISTANT", "SYSTEM", "TOOL"}:
            raise ValueError(f"Unsupported chat role: {role}")

        raw_content = message.get("content", "")
        if isinstance(raw_content, str):
            text = raw_content
        elif isinstance(raw_content, Sequence):
            text = "\n".join(
                str(part.get("text", "")) if isinstance(part, Mapping) else str(part)
                for part in raw_content
            )
        else:
            text = str(raw_content)

        content = models.TextContent()
        content.text = text
        if role == "TOOL":
            oci_message = models.ToolMessage(tool_call_id=message["tool_call_id"])
        elif role == "ASSISTANT" and message.get("tool_calls"):
            oci_message = models.AssistantMessage(tool_calls=[models.FunctionCall(
                id=call["id"], name=call["name"], arguments=call["arguments"]
            ) for call in message["tool_calls"]])
        else:
            oci_message = models.Message()
        oci_message.role = role
        oci_message.content = [content]
        return oci_message
