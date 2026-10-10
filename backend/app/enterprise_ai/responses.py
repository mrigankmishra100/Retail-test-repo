"""OCI Responses model adapter. No automatic native-Chat fallback."""

import asyncio
from collections.abc import Mapping, Sequence
import logging
from functools import cached_property
from typing import Any

from app.enterprise_ai.oci_client import OCIGenerativeAIClient

logger = logging.getLogger(__name__)


class EnterpriseAIResponsesClient:
    """Model-independent interface for bounded LangGraph tool workflows.

    OCI Responses uses OCI IAM or GenAI-key auth,
    OpenAI-compatible function items, and optional server-owned conversations.
    """

    def __init__(
        self,
        endpoint: str | None = None,
        model_id: str | None = None,
        compartment_id: str | None = None,
        config_file: str = "~/.oci/config",
        config_profile: str = "DEFAULT",
        enabled: bool = False,
        max_tokens: int = 6000,
        temperature: float = 1.0,
        top_p: float = 0.95,
        top_k: int = 1,
        auth_mode: str = 'api_key',
        api_mode: str = 'responses',
        project_id: str | None = None,
        timeout: float = 60,
        responses_auth_mode: str = 'oci',
        genai_api_key: Any = None,
        vector_store_ids=None,
        observability=None,
    ) -> None:
        self.endpoint = endpoint
        self.model_id = model_id
        self.compartment_id = compartment_id
        self.config_file = config_file
        self.config_profile = config_profile
        self.enabled = enabled
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.top_p = top_p
        self.top_k = top_k
        if auth_mode not in {'api_key', 'resource_principal', 'instance_principal'}:
            raise ValueError('Unsupported OCI authentication mode')
        self.auth_mode = auth_mode
        if api_mode not in {'chat', 'responses'}:
            raise ValueError('Unsupported OCI model API mode')
        if enabled and api_mode != 'responses':
            raise ValueError('Enabled AI requires OCI Responses API mode')
        self.api_mode, self.project_id, self.timeout = api_mode, project_id, timeout
        if responses_auth_mode not in {'oci', 'genai_api_key'}:
            raise ValueError('Unsupported Responses authentication mode')
        if enabled and api_mode != 'responses' and responses_auth_mode == 'genai_api_key':
            raise ValueError('GenAI API-key authentication requires Responses API mode')
        self.responses_auth_mode = responses_auth_mode
        self._genai_api_key = genai_api_key
        self.vector_store_ids = list(vector_store_ids or [])
        self.observability = observability
        # The OCI SDK client is deliberately created only when a request is made.

    @classmethod
    def from_settings(cls, settings: Any, observability=None) -> "EnterpriseAIResponsesClient":
        """Create the adapter from the application's environment-backed settings."""
        return cls(
            endpoint=settings.enterprise_ai_endpoint,
            model_id=settings.enterprise_ai_model_id,
            compartment_id=settings.oci_compartment_id,
            config_file=settings.oci_config_file,
            config_profile=settings.oci_config_profile,
            enabled=settings.enterprise_ai_responses_enabled,
            max_tokens=getattr(settings, "enterprise_ai_max_tokens", 6000),
            temperature=getattr(settings, "enterprise_ai_temperature", 1.0),
            top_p=getattr(settings, "enterprise_ai_top_p", 0.95),
            top_k=getattr(settings, "enterprise_ai_top_k", 1),
            auth_mode=getattr(settings, 'oci_auth_mode', 'api_key'),
            api_mode=getattr(settings, 'enterprise_ai_api_mode', 'responses'),
            project_id=getattr(settings, 'enterprise_ai_project_id', None),
            timeout=getattr(settings, 'enterprise_ai_timeout_seconds', 60),
            responses_auth_mode=getattr(settings, 'enterprise_ai_auth_mode', 'oci'),
            genai_api_key=getattr(settings, 'oci_genai_api_key', None),
            vector_store_ids=(getattr(settings, 'enterprise_ai_vector_store_ids', [])
                              if getattr(settings, 'enterprise_ai_file_search_enabled', False) else []),
            observability=observability,
        )

    @cached_property
    def transport(self):
        from app.enterprise_ai.responses_transport import OCIResponsesTransport
        return OCIResponsesTransport(endpoint=self.endpoint, project_id=self.project_id,
            auth_mode=self.auth_mode, config_file=self.config_file,
            config_profile=self.config_profile, timeout=self.timeout,
            responses_auth_mode=self.responses_auth_mode, genai_api_key=self._genai_api_key)

    async def create_response(
        self,
        messages: Sequence[Mapping[str, Any]],
        conversation_id: str | None = None,
        tools: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Create an LLM response through OCI Generative AI when configured."""
        return await asyncio.to_thread(self.create_response_sync, messages,
                                       conversation_id=conversation_id, tools=tools)

    def create_response_sync(self, messages, conversation_id=None, tools=None):
        """Synchronous boundary for sync LangGraph nodes; native OCI function calling."""
        if self.observability is None:
            return self._create_response_sync(messages, conversation_id=conversation_id, tools=tools)
        from app.observability.context import TraceContext, request_trace_id
        with self.observability.observation(
            "oci.generative_ai.response",
            as_type="generation",
            input={"messages": messages, "conversation_id": conversation_id,
                   "tool_names": [tool.get("name") for tool in tools or ()]},
            metadata={"provider": "oci", "model": self.model_id, "api_mode": self.api_mode},
            context=TraceContext(trace_id=request_trace_id.get(), conversation_id=conversation_id,
                                 agent="retail"),
            model=self.model_id,
        ) as observation:
            result = self._create_response_sync(messages, conversation_id=conversation_id, tools=tools)
            observation.update(
                output={"status": result.get("status"), "output": result.get("output"),
                        "tool_calls": result.get("tool_calls") or []},
                metadata={"status": result.get("status"),
                          "tool_count": len(result.get("tool_calls") or [])},
                usage_details=result.get("usage"),
            )
            return result

    def _create_response_sync(self, messages, conversation_id=None, tools=None):
        """Execute one OCI model request; the public boundary adds optional tracing."""
        """Synchronous OCI Responses boundary for model calls and function tools."""
        if not self._is_configured:
            if self.enabled:
                logger.error('ai_configuration_failed', extra={'stage': 'configuration',
                    'reason': 'incomplete_responses_configuration', 'status': 'failed'})
                raise RuntimeError('OCI Responses configuration is incomplete')
            logger.info('ai_request_skipped', extra={'reason': 'responses_disabled', 'status': 'disabled'})
            return {
                "status": "not_configured",
                "output": None,
                "conversation_id": conversation_id,
                "message_count": len(messages),
                "tool_count": len(tools or ()),
            }
        if not messages:
            raise ValueError("At least one chat message is required")
        if self.api_mode != 'responses':
            raise RuntimeError('Enabled AI requires OCI Responses API mode')
        return self._create_responses_response(messages, conversation_id=conversation_id, tools=tools)

    @property
    def _is_configured(self) -> bool:
        return bool(
            self.enabled
            and self.endpoint
            and self.model_id
            and (self.project_id if self.api_mode == 'responses' else self.compartment_id)
            and (self._genai_api_key if self.responses_auth_mode == 'genai_api_key'
                 else self.auth_mode != 'api_key' or (self.config_file and self.config_profile))
        )

    def _create_responses_response(self, messages, *, conversation_id=None, tools=None):
        """Translate server-built functions and explicitly configured policy File Search."""
        from app.enterprise_ai.responses_transport import conversation_path, OCIResponsesUnavailable
        items, instructions = [], []
        for message in messages:
            role, content = message['role'].lower(), message.get('content', '')
            if role == 'system':
                instructions.append(content)
            elif role == 'tool':
                items.append({'type': 'function_call_output', 'call_id': message['tool_call_id'], 'output': content})
            elif role == 'assistant' and 'response_items' in message:
                # Preserve reasoning/signature items for stateless continuations.
                # This field is produced by this adapter, never accepted by /chat.
                items.extend(message['response_items'])
            elif role in {'user', 'assistant'}:
                if content:
                    items.append({'role': role, 'content': content})
                for call in message.get('tool_calls', []):
                    items.append({'type': 'function_call', 'call_id': call['id'],
                                  'name': call['name'], 'arguments': call['arguments']})
            else:
                raise ValueError('Unsupported Responses message role')
        payload = {'model': self.model_id, 'input': items, 'max_output_tokens': self.max_tokens,
                   'store': bool(conversation_id)}
        if instructions:
            payload['instructions'] = '\n\n'.join(instructions)
        if conversation_id:
            conversation_path(conversation_id)
            payload['conversation'] = conversation_id
        if tools:
            payload['tools'] = [{'type': 'function', 'name': t['name'],
                'description': t.get('description') or '', 'parameters': t['parameters'],
                'strict': False} for t in tools]
            payload['parallel_tool_calls'] = False
        if self.vector_store_ids:
            payload.setdefault('tools', []).append({'type': 'file_search',
                'vector_store_ids': self.vector_store_ids, 'max_num_results': 5})
            payload['max_tool_calls'] = 8
        response = self.transport.request('POST', '/responses', payload)
        if response.get('status') != 'completed':
            raise OCIResponsesUnavailable('OCI response did not complete')
        output = response.get('output')
        if not isinstance(output, list):
            raise OCIResponsesUnavailable('Invalid OCI response output')
        calls, text, ids = [], [], set()
        for item in output:
            if not isinstance(item, dict):
                raise OCIResponsesUnavailable('Invalid OCI response item')
            kind = item.get('type')
            if kind == 'function_call':
                call_id, name, arguments = (item.get(k) for k in ('call_id', 'name', 'arguments'))
                if (not isinstance(call_id, str) or not 1 <= len(call_id) <= 256 or call_id in ids
                        or not isinstance(name, str) or not 1 <= len(name) <= 64
                        or not isinstance(arguments, str) or len(arguments) > 8000 or len(calls) >= 8):
                    raise OCIResponsesUnavailable('Invalid OCI function call')
                ids.add(call_id)
                calls.append({'id': call_id, 'name': name, 'arguments': arguments})
            elif kind == 'message':
                if item.get('role') != 'assistant' or not isinstance(item.get('content'), list):
                    raise OCIResponsesUnavailable('Invalid OCI assistant message')
                for part in item['content']:
                    if not isinstance(part, dict):
                        raise OCIResponsesUnavailable('Invalid OCI message content')
                    value = part.get('text') if part.get('type') == 'output_text' else part.get('refusal')
                    if not isinstance(value, str):
                        raise OCIResponsesUnavailable('Unsupported OCI message content')
                    text.append(value)
            elif kind == 'file_search_call' and self.vector_store_ids:
                if item.get('status') != 'completed':
                    raise OCIResponsesUnavailable('Policy File Search did not complete')
            elif kind != 'reasoning':
                raise OCIResponsesUnavailable('Unsupported OCI response item')
        return {'status': 'completed', 'output': '\n'.join(text) or None, 'tool_calls': calls,
                'conversation_id': conversation_id, 'model_id': self.model_id,
                'response_items': output, 'usage': self._extract_usage(response)}

    @staticmethod
    def _extract_usage(response: Any) -> dict[str, int] | None:
        """Normalize OCI Chat/Responses token usage for Langfuse generations."""
        data = response.get("data") if isinstance(response, Mapping) else getattr(response, "data", None)
        chat = data.get("chat_response") if isinstance(data, Mapping) else getattr(data, "chat_response", None)
        candidates = [
            response.get("usage") if isinstance(response, Mapping) else getattr(response, "usage", None),
            data.get("usage") if isinstance(data, Mapping) else getattr(data, "usage", None),
            chat.get("usage") if isinstance(chat, Mapping) else getattr(chat, "usage", None),
        ]

        def value(source: Any, *names: str) -> int | None:
            for name in names:
                raw = source.get(name) if isinstance(source, Mapping) else getattr(source, name, None)
                if isinstance(raw, int) and raw >= 0:
                    return raw
            return None

        for candidate in candidates:
            if candidate is None:
                continue
            prompt = value(candidate, "prompt_tokens", "input_tokens")
            completion = value(candidate, "completion_tokens", "output_tokens")
            total = value(candidate, "total_tokens")
            if prompt is None and completion is None and total is None:
                continue
            usage = {}
            if prompt is not None:
                usage["prompt_tokens"] = prompt
            if completion is not None:
                usage["completion_tokens"] = completion
            if total is not None:
                usage["total_tokens"] = total
            elif prompt is not None and completion is not None:
                usage["total_tokens"] = prompt + completion
            return usage
        return None

    def _create_chat_response(self, messages: Sequence[Mapping[str, Any]], *, tools=None) -> Any:
        """Delegate the OCI request to the shared Generative AI client."""
        logger.info("Calling the configured OCI Generative AI model")
        return OCIGenerativeAIClient(
            endpoint=self.endpoint,
            model_id=self.model_id,
            compartment_id=self.compartment_id,
            config_file=self.config_file,
            config_profile=self.config_profile,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            top_p=self.top_p,
            top_k=self.top_k,
            auth_mode=self.auth_mode,
        ).chat(messages, tools=tools)

    @staticmethod
    def _extract_tool_calls(response):
        choices = getattr(getattr(getattr(response, "data", None), "chat_response", None), "choices", None) or []
        message = getattr(choices[0], "message", None) if choices else None
        calls = getattr(message, "tool_calls", None) or []
        if len(calls) > 8:
            raise RuntimeError("Model tool-call limit exceeded")
        result = []
        ids = set()
        for call in calls:
            call_id, name, arguments = (getattr(call, key, None) for key in ("id", "name", "arguments"))
            if (getattr(call, "type", "FUNCTION") != "FUNCTION"
                    or not isinstance(call_id, str) or not 1 <= len(call_id) <= 256 or call_id in ids
                    or not isinstance(name, str) or not 1 <= len(name) <= 64
                    or not isinstance(arguments, str) or len(arguments) > 8000):
                raise RuntimeError("Invalid model tool call")
            ids.add(call_id)
            result.append({"id": call_id, "name": name, "arguments": arguments})
        return result

    @staticmethod
    def _extract_text(response: Any) -> str | None:
        """Extract text from the OCI generic chat response without exposing SDK internals."""
        data = getattr(response, "data", None)
        chat_response = getattr(data, "chat_response", None)
        choices = getattr(chat_response, "choices", None) or []
        if not choices:
            return getattr(chat_response, "text", None)
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None) or []
        text_parts = [getattr(part, "text", "") for part in content]
        combined = "\n".join(part for part in text_parts if part)
        return combined or None
