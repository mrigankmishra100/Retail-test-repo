"""Evaluation-only OCI Generic Chat adapter using server-owned DEFAULT credentials."""
from pathlib import Path
from app.enterprise_ai.oci_client import OCIGenerativeAIClient
from app.enterprise_ai.responses import EnterpriseAIResponsesClient
from app.live_activity import activity_step


class NativeChatUnavailable(RuntimeError):
    def __init__(self, code, status=None):
        super().__init__('Native OCI Chat request unavailable')
        self.public_code, self.http_status = code, status


class NativeChatEvaluationClient:
    enabled = True
    api_mode = 'native_chat'
    vector_store_ids = ()

    def __init__(self, regional, *, config_file, timeout=240):
        self.model_id = regional.model_id
        self.endpoint = regional.endpoint
        self.compartment_id = regional.compartment_id
        self.config_file = config_file
        self.config_profile = 'DEFAULT'
        self.max_tokens = regional.max_completion_tokens
        self.timeout = timeout

    @activity_step('model', 'Generating response')
    def create_response_sync(self, messages, conversation_id=None, tools=None, require_file_search=False):
        if conversation_id or require_file_search:
            raise ValueError('Native Chat evaluation does not support managed conversations or File Search')
        if not messages:
            raise ValueError('At least one message is required')
        import oci
        models = oci.generative_ai_inference.models
        config = oci.config.from_file(str(Path(self.config_file).expanduser()), 'DEFAULT')
        client = oci.generative_ai_inference.GenerativeAiInferenceClient(
            config=config, service_endpoint=self.endpoint,
            retry_strategy=oci.retry.NoneRetryStrategy(), timeout=(10, self.timeout))
        request = models.GenericChatRequest(
            messages=[OCIGenerativeAIClient._to_oci_message(models, message) for message in messages],
            max_completion_tokens=self.max_tokens, verbosity='MEDIUM')
        if tools:
            request.tools = [models.FunctionDefinition(name=t['name'], description=t.get('description', ''),
                                                      parameters=t['parameters']) for t in tools]
            request.tool_choice = models.ToolChoiceAuto()
            request.is_parallel_tool_calls = False
        try:
            response = client.chat(models.ChatDetails(
                compartment_id=self.compartment_id,
                serving_mode=models.OnDemandServingMode(model_id=self.model_id), chat_request=request))
        except oci.exceptions.ServiceError as exc:
            # Classify known provider failures; never persist or return its raw text.
            message = str(exc.message).lower()
            if 'function tools with reasoning_effort' in message and 'not supported' in message:
                code = 'native_tools_require_responses'
            else:
                code = {400: 'native_request_rejected', 401: 'native_authentication_failed',
                        403: 'native_access_denied', 404: 'native_model_unavailable',
                        429: 'native_rate_limited'}.get(exc.status, 'native_service_unavailable')
            raise NativeChatUnavailable(code, exc.status) from None
        chat = response.data.chat_response
        choices = getattr(chat, 'choices', None) or []
        if not choices:
            raise RuntimeError('Native model returned no choices')
        # Do not present truncated or filtered completions as successful answers.
        reason = str(getattr(choices[0], 'finish_reason', '') or '').upper()
        if reason not in {'STOP', 'TOOL_CALLS'}:
            raise RuntimeError('Native model response did not complete')
        usage = getattr(chat, 'usage', None) or getattr(choices[0], 'usage', None)
        normalized = None
        if usage is not None:
            normalized = {'input_tokens': getattr(usage, 'prompt_tokens', None),
                          'output_tokens': getattr(usage, 'completion_tokens', None),
                          'input_tokens_details': {'cached_tokens': getattr(getattr(usage, 'prompt_tokens_details', None), 'cached_tokens', None)},
                          'output_tokens_details': {'reasoning_tokens': getattr(getattr(usage, 'completion_tokens_details', None), 'reasoning_tokens', None)}}
        calls = EnterpriseAIResponsesClient._extract_tool_calls(response)
        output = EnterpriseAIResponsesClient._extract_text(response)
        if not calls and not output:
            raise RuntimeError('Native model returned an empty response')
        return {'status': 'completed', 'output': output, 'tool_calls': calls,
                'conversation_id': None, 'model_id': self.model_id, 'usage': normalized,
                'builtin_tools': [], 'citations': []}
