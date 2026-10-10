"""OCI conversation lifecycle. Session ownership stays in the application service."""
import asyncio
from app.enterprise_ai.responses_transport import conversation_path


class EnterpriseAIConversationClient:
    def __init__(self, endpoint=None, enabled=False, responses=None):
        self.endpoint, self.enabled, self.responses = endpoint, enabled, responses

    def _transport(self):
        if not self.enabled or self.responses is None or self.responses.api_mode != 'responses':
            raise RuntimeError('OCI Conversations is not configured')
        return self.responses.transport

    def create_sync(self):
        result = self._transport().request('POST', '/conversations',
            {'metadata': {'application': 'retail-inventory'}})
        identifier = result.get('id')
        conversation_path(identifier)
        return identifier

    def get_sync(self, conversation_id):
        return self._transport().request('GET', conversation_path(conversation_id))

    def delete_sync(self, conversation_id):
        return self._transport().request('DELETE', conversation_path(conversation_id))

    async def create_conversation(self, session_id):
        if not self.enabled:
            return {'status': 'not_configured', 'session_id': session_id, 'conversation_id': None}
        # Raw application session identifiers are never sent to OCI metadata.
        identifier = await asyncio.to_thread(self.create_sync)
        return {'status': 'completed', 'session_id': session_id, 'conversation_id': identifier}

    async def get_conversation(self, conversation_id):
        if not self.enabled:
            return {'status': 'not_configured', 'conversation_id': conversation_id, 'context': None}
        return await asyncio.to_thread(self.get_sync, conversation_id)
