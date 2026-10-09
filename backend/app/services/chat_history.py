"""Durable, session-owned retail conversation history in Oracle."""

import json
from uuid import uuid4

from app.services.cases import CaseConflict, CaseNotFound, CaseService


class ChatHistoryService:
    def __init__(self, database):
        self.database = database

    @staticmethod
    def _identity(session):
        value = CaseService._session(session)
        value.require_role("retail-manager")
        return value

    def get(self, conversation_id, workflow_thread_id=None, *, session, limit=8):
        actor = self._identity(session)
        conversation_id = str(conversation_id)
        workflow_thread_id = str(workflow_thread_id) if workflow_thread_id else None
        rows = self.database.fetch_all("chat_conversation", {"conversation_id": conversation_id})
        if not rows:
            return {"conversation_id": conversation_id, "workflow_thread_id": workflow_thread_id,
                    "exists": False, "next_turn": 1, "messages": []}
        conversation = rows[0]
        if (conversation["owner_session_id"] != str(actor.session_id)
                or conversation["owner_role"] != actor.role):
            raise CaseNotFound("Conversation not found")
        if conversation.get("workflow_thread_id") != workflow_thread_id:
            raise CaseConflict("Keep the conversation's workflow_thread_id unchanged")
        messages = self.database.fetch_all("chat_messages_page", {
            "conversation_id": conversation_id, "page_size": limit,
        })
        result = []
        for message in messages:
            evidence = message.get("evidence_metadata")
            if isinstance(evidence, str):
                evidence = json.loads(evidence)
            result.append({**message, "role": message["role"].lower(),
                           "evidence_metadata": evidence})
        next_turn = max((message["turn_number"] for message in result), default=0) + 1
        return {"conversation_id": conversation_id,
                "workflow_thread_id": conversation.get("workflow_thread_id"),
                "exists": True, "next_turn": next_turn, "messages": result}

    def save_turn(self, conversation_id, workflow_thread_id, turn_number, user_message,
                  assistant_message, response_route, response_status, guardrail_status,
                  evidence_metadata, error_code=None, *, session):
        actor = self._identity(session)
        metadata = json.dumps(evidence_metadata or {}, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False, allow_nan=False)
        if len(metadata) > 16000:
            raise ValueError("Chat evidence metadata is too large")
        self.database.execute("save_chat_turn", {
            "conversation_id": str(conversation_id),
            "owner_session_id": str(actor.session_id),
            "owner_role": actor.role,
            "workflow_thread_id": str(workflow_thread_id) if workflow_thread_id else None,
            "user_message_id": str(uuid4()),
            "assistant_message_id": str(uuid4()),
            "turn_number": turn_number,
            "user_content": user_message,
            "assistant_content": assistant_message,
            "response_route": response_route,
            "response_status": response_status,
            "guardrail_status": guardrail_status,
            "evidence_metadata": metadata,
            "error_code": error_code,
        })
        return self.get(conversation_id, workflow_thread_id, session=actor, limit=8)
