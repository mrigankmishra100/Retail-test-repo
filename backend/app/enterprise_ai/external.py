"""Direct-provider adapters for the existing response/function-call interface."""
import json
import re
from uuid import uuid4
import httpx
from app.enterprise_ai.responses import EnterpriseAIResponsesClient
from app.live_activity import activity_step

EXTERNAL_ERROR_MESSAGES = {
    'external_model_not_found': 'The selected model is not available through this provider API for your key. Open External model and load available models before choosing one.',
    'external_model_method_unsupported': 'This model does not support content generation through the selected API. Choose another listed text model.',
    'external_rate_limit': 'The external provider rejected the request because of quota or rate limits. Check the provider limits before retrying.',
    'external_authentication_failed': 'The external provider rejected the API key or its permissions. Reconnect with an authorized provider key.',
    'external_model_unavailable': 'The external provider could not be reached. Check connectivity and provider availability.',
    'external_model_request_failed': 'The external provider rejected the model request. Check the model, API access and provider limits.',
    'external_response_incomplete': 'The external model did not complete its answer. Check the output limit or try another model.',
}


class ExternalModelUnavailable(RuntimeError):
    def __init__(self, code='external_model_unavailable', *, status=None, result=None):
        super().__init__('External model unavailable. Check the provider, model ID, API key and quota.')
        self.public_code, self.http_status = code, status
        self.chat_result = result or {}


class ExternalModelClient(EnterpriseAIResponsesClient):
    def __init__(self, config):
        super().__init__(model_id=config.model_id, max_tokens=config.max_tokens, enabled=False)
        self.enabled, self.provider, self._key = True, config.provider, config.api_key
        self.vector_store_ids = []
        self.endpoint = {'openai': 'https://api.openai.com/v1', 'gemini': 'https://generativelanguage.googleapis.com/v1beta',
                         'anthropic': 'https://api.anthropic.com/v1'}[self.provider]
        self.transport = self

    def request(self, method, path, payload):
        # OpenAI reuses the existing stateless Responses serializer/parser.
        if self.provider != 'openai' or method != 'POST' or path != '/responses':
            raise ValueError('Unsupported external provider operation')
        payload['include'] = ['reasoning.encrypted_content']
        return self._post(path, payload)

    def _post(self, path, payload):
        return self._http('POST', path, payload)

    def _get(self, path, params=None):
        return self._http('GET', path, params=params)

    def _http(self, method, path, payload=None, params=None):
        key = self._key.get_secret_value()
        headers = {'Content-Type': 'application/json'}
        if self.provider == 'openai': headers['Authorization'] = 'Bearer ' + key
        elif self.provider == 'gemini': headers['x-goog-api-key'] = key
        else: headers.update({'x-api-key': key, 'anthropic-version': '2023-06-01'})
        try:
            # Fixed public provider origins, no redirects, proxies or automatic retries.
            with httpx.Client(timeout=15 if method == 'GET' else 90, follow_redirects=False, trust_env=False,
                              transport=getattr(self, 'http_transport', None)) as client:
                with client.stream(method, self.endpoint + path, headers=headers, json=payload, params=params) as response:
                    if response.status_code != 200:
                        code = 'external_model_not_found' if response.status_code == 404 else 'external_rate_limit' if response.status_code == 429 else 'external_authentication_failed' if response.status_code in {401, 403} else 'external_model_request_failed'
                        raise ExternalModelUnavailable(code, status=response.status_code)
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > 1_000_000: raise ExternalModelUnavailable('external_response_too_large')
                        chunks.append(chunk)
                    result = json.loads(b''.join(chunks))
                    if not isinstance(result, dict): raise ValueError('Invalid response')
                    return result
        except ExternalModelUnavailable:
            raise
        except Exception:
            # Never surface provider exception text or bodies, which may echo submitted secrets/data.
            raise ExternalModelUnavailable() from None

    def validate_connection(self):
        if self.provider == 'gemini':
            metadata = self._get('/models/' + self.model_id)
            if metadata.get('name') != 'models/' + self.model_id:
                raise ExternalModelUnavailable('external_model_not_found', status=404)
            if 'generateContent' not in metadata.get('supportedGenerationMethods', []):
                raise ExternalModelUnavailable('external_model_method_unsupported')

    def available_models(self):
        if self.provider != 'gemini':
            return self._available_provider_models()
        models, token = set(), None
        for _ in range(5):
            params = {'pageSize': 1000, **({'pageToken': token} if token else {})}
            data = self._get('/models', params)
            if not isinstance(data.get('models'), list): raise ExternalModelUnavailable('external_response_invalid')
            for row in data['models']:
                name = row.get('name', '') if isinstance(row, dict) else ''
                if (isinstance(name, str) and re.fullmatch(r'models/gemini-[A-Za-z0-9._-]{1,140}', name)
                        and isinstance(row.get('supportedGenerationMethods'), list)
                        and 'generateContent' in row['supportedGenerationMethods']
                        and not any(word in name.lower() for word in ('image', 'audio', 'tts', 'robotics', 'embedding'))):
                    models.add(name.removeprefix('models/'))
            if len(models) > 1000: raise ExternalModelUnavailable('external_response_too_large')
            token = data.get('nextPageToken')
            if not token: return sorted(models)
            if not isinstance(token, str) or len(token) > 4096: raise ExternalModelUnavailable('external_response_invalid')
        raise ExternalModelUnavailable('external_response_too_large')

    def _available_provider_models(self):
        # Catalog membership is not a quota, billing or function-calling guarantee.
        models, cursor, seen = set(), None, set()
        for _ in range(5):
            params = ({'limit': 1000, **({'after_id': cursor} if cursor else {})}
                      if self.provider == 'anthropic' else None)
            data = self._get('/models', params)
            if not isinstance(data.get('data'), list):
                raise ExternalModelUnavailable('external_response_invalid')
            for row in data['data']:
                identifier = row.get('id') if isinstance(row, dict) else None
                if (isinstance(identifier, str)
                        and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,149}', identifier)
                        and row.get('lifecycle') != 'retired'):
                    models.add(identifier)
            if len(models) > 1000:
                raise ExternalModelUnavailable('external_response_too_large')
            if self.provider == 'openai':
                return sorted(models)
            if not isinstance(data.get('has_more'), bool):
                raise ExternalModelUnavailable('external_response_invalid')
            if not data['has_more']:
                return sorted(models)
            cursor = data.get('last_id')
            if not isinstance(cursor, str) or not cursor or len(cursor) > 4096 or cursor in seen:
                raise ExternalModelUnavailable('external_response_invalid')
            seen.add(cursor)
        raise ExternalModelUnavailable('external_response_too_large')

    def create_response_sync(self, messages, conversation_id=None, tools=None, require_file_search=False, allow_tools=True):
        return self._traced_response(messages, conversation_id=conversation_id, tools=tools, require_file_search=require_file_search, allow_tools=allow_tools)

    def _create_response_sync(self, messages, conversation_id=None, tools=None, require_file_search=False, allow_tools=True):
        if not allow_tools:
            tools = []
        if conversation_id or require_file_search:
            raise ValueError('External models use application history and function tools; OCI hosted File Search is unavailable')
        try:
            if self.provider == 'openai':
                return self._create_responses_response(messages, tools=tools, allow_tools=allow_tools)
            return self._create_native_response(messages, tools)
        except ExternalModelUnavailable:
            raise
        except Exception as exc:
            raise ExternalModelUnavailable('external_response_invalid', result=getattr(exc, 'chat_result', None)) from None

    @activity_step('model', 'Generating response')
    def _create_native_response(self, messages, tools):
        payload = self._gemini_payload(messages, tools) if self.provider == 'gemini' else self._anthropic_payload(messages, tools)
        path = f'/models/{self.model_id}:generateContent' if self.provider == 'gemini' else '/messages'
        raw = self._post(path, payload)
        result = self._gemini_result(raw) if self.provider == 'gemini' else self._anthropic_result(raw)
        return self._validated(result)

    @staticmethod
    def _json(value):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)

    @staticmethod
    def _object(content):
        try: return json.loads(content)
        except (ValueError, TypeError): return {'text': content}

    def _gemini_payload(self, messages, tools):
        contents, system, calls = [], [], {}
        for message in messages:
            role, content = message['role'], message.get('content', '')
            if role == 'system':
                system.append({'text': content}); continue
            if role == 'tool':
                call = calls[message['tool_call_id']]
                response = {'name': call['name'], 'response': {'result': self._object(content)}}
                if call.get('provider_id'): response['id'] = call['provider_id']
                parts = [{'functionResponse': response}]
                role = 'user'
            elif role == 'assistant':
                parts = message.get('response_items')
                if parts is None:
                    parts = ([{'text': content}] if content else []) + [
                        {'functionCall': {'name': c['name'], 'args': self._object(c['arguments'])}} for c in message.get('tool_calls', [])]
                raw_calls = [p['functionCall'] for p in parts if 'functionCall' in p]
                for call, raw in zip(message.get('tool_calls', []), raw_calls):
                    calls[call['id']] = {'name': call['name'], 'provider_id': raw.get('id')}
                role = 'model'
            else: parts = [{'text': content}]
            if not parts: continue
            if contents and contents[-1]['role'] == role: contents[-1]['parts'].extend(parts)
            else: contents.append({'role': role, 'parts': parts})
        payload = {'contents': contents, 'generationConfig': {'maxOutputTokens': self.max_tokens}}
        if system: payload['systemInstruction'] = {'parts': system}
        if tools:
            payload['tools'] = [{'functionDeclarations': [{'name': t['name'], 'description': t.get('description') or '',
                'parametersJsonSchema': t['parameters']} for t in tools]}]
        return payload

    def _anthropic_payload(self, messages, tools):
        transcript, system = [], []
        for message in messages:
            role, content = message['role'], message.get('content', '')
            if role == 'system': system.append(content); continue
            if role == 'tool':
                role, blocks = 'user', [{'type': 'tool_result', 'tool_use_id': message['tool_call_id'], 'content': content}]
            elif role == 'assistant':
                blocks = message.get('response_items')
                if blocks is None:
                    blocks = ([{'type': 'text', 'text': content}] if content else []) + [
                        {'type': 'tool_use', 'id': c['id'], 'name': c['name'], 'input': self._object(c['arguments'])} for c in message.get('tool_calls', [])]
            else: blocks = [{'type': 'text', 'text': content}]
            if not blocks: continue
            if transcript and transcript[-1]['role'] == role: transcript[-1]['content'].extend(blocks)
            else: transcript.append({'role': role, 'content': blocks})
        payload = {'model': self.model_id, 'messages': transcript, 'max_tokens': self.max_tokens}
        if system: payload['system'] = '\n\n'.join(system)
        if tools: payload['tools'] = [{'name': t['name'], 'description': t.get('description') or '', 'input_schema': t['parameters']} for t in tools]
        return payload

    def _gemini_result(self, raw):
        candidates, usage = raw.get('candidates', []), raw.get('usageMetadata', {})
        thought, output = usage.get('thoughtsTokenCount', 0), usage.get('candidatesTokenCount')
        normalized = {'input_tokens': usage.get('promptTokenCount'), 'output_tokens': output + thought if isinstance(output, int) and isinstance(thought, int) else None,
            'input_tokens_details': {'cached_tokens': usage.get('cachedContentTokenCount', 0)}, 'output_tokens_details': {'reasoning_tokens': thought}}
        if not candidates or candidates[0].get('finishReason') != 'STOP':
            raise ExternalModelUnavailable('external_response_incomplete', result={'usage': normalized})
        parts = candidates[0].get('content', {}).get('parts', [])
        calls, text = [], []
        for part in parts:
            if 'functionCall' in part:
                call = part['functionCall']
                calls.append({'id': call.get('id') or 'call_' + uuid4().hex, 'name': call['name'], 'arguments': self._json(call.get('args', {}))})
            elif 'text' in part and not part.get('thought'): text.append(part['text'])
        return {'status': 'completed', 'output': '\n'.join(text), 'tool_calls': calls, 'response_items': parts,
                'usage': normalized, 'model_id': self.model_id, 'builtin_tools': [], 'citations': []}

    def _anthropic_result(self, raw):
        usage = raw.get('usage', {})
        cached, written, incoming = usage.get('cache_read_input_tokens', 0), usage.get('cache_creation_input_tokens', 0), usage.get('input_tokens')
        normalized = {'input_tokens': incoming + cached + written if all(isinstance(v, int) for v in (incoming, cached, written)) else None,
            'output_tokens': usage.get('output_tokens'), 'input_tokens_details': {'cached_tokens': cached, 'cache_write_tokens': written}}
        if raw.get('stop_reason') not in {'end_turn', 'tool_use'}:
            raise ExternalModelUnavailable('external_response_incomplete', result={'usage': normalized})
        blocks = raw.get('content', [])
        return {'status': 'completed', 'output': '\n'.join(p['text'] for p in blocks if p.get('type') == 'text'),
            'tool_calls': [{'id': p['id'], 'name': p['name'], 'arguments': self._json(p['input'])} for p in blocks if p.get('type') == 'tool_use'],
            'response_items': blocks, 'usage': normalized, 'model_id': self.model_id, 'builtin_tools': [], 'citations': []}

    def _validated(self, result):
        calls = result['tool_calls']
        if len(calls) > 8 or len({c['id'] for c in calls}) != len(calls): raise ValueError('Invalid tool batch')
        for call in calls:
            if (not isinstance(call['id'], str) or not 1 <= len(call['id']) <= 256 or not isinstance(call['name'], str)
                    or not 1 <= len(call['name']) <= 64 or len(call['arguments']) > 8000
                    or not isinstance(json.loads(call['arguments']), dict)): raise ValueError('Invalid tool arguments')
        if not isinstance(result['output'], str) or len(result['output']) > 16000 or not (result['output'].strip() or calls):
            raise ValueError('Empty or oversized response')
        return result
