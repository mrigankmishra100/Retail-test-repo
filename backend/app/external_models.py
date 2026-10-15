"""Session-owned external model connections. Secrets never enter checkpoints or catalogs."""
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Literal
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from app.chat_models import ChatModelProfile
from app.services.cases import CaseConflict

PROVIDERS = {'gemini': 'Google Gemini', 'openai': 'OpenAI', 'anthropic': 'Anthropic'}


class ExternalModelInput(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    provider: Literal['gemini', 'openai', 'anthropic']
    model_id: str = Field(min_length=1, max_length=150, pattern=r'^[A-Za-z0-9][A-Za-z0-9._:-]*$')
    api_key: SecretStr = Field(repr=False)
    max_tokens: int = Field(default=4096, ge=256, le=16000)

    @field_validator('api_key')
    @classmethod
    def key(cls, value):
        raw = value.get_secret_value()
        if not 12 <= len(raw) <= 512 or any(c.isspace() or ord(c) < 33 or ord(c) > 126 for c in raw):
            raise ValueError('Enter a valid provider API key')
        return value

    def wire(self):
        # Only for the existing authenticated HTTPS backend-to-agent transport.
        return {**self.model_dump(mode='json'), 'api_key': self.api_key.get_secret_value()}


class ExternalModelConnection(BaseModel):
    profile: ChatModelProfile
    expires_at: datetime


class ExternalModelDiscoveryInput(ExternalModelInput):
    provider: Literal['gemini', 'openai', 'anthropic'] = 'gemini'
    model_id: str = Field(default='catalog', pattern=r'^[A-Za-z0-9][A-Za-z0-9._:-]*$')


class ExternalModelOptions(BaseModel):
    models: list[str] = Field(max_length=1000)


@dataclass(repr=False)
class Connection:
    owner: str
    profile: ChatModelProfile
    config: ExternalModelInput
    expires_at: datetime


class ExternalModelStore:
    """Bounded in-process vault. Restart/redeploy requires reconnection; never fall back."""
    def __init__(self):
        self.connections, self.lock = {}, Lock()

    def _prune(self):
        now = datetime.now(timezone.utc)
        self.connections = {k: v for k, v in self.connections.items() if v.expires_at > now}

    def register(self, config, session):
        session.require_role('retail-manager')
        with self.lock:
            self._prune()
            if len(self.connections) >= 200 or sum(v.owner == str(session.session_id) for v in self.connections.values()) >= 5:
                raise CaseConflict('External connection limit reached; use an existing connection or start a new session')
            profile = ChatModelProfile(id='external-' + uuid4().hex, label=f'{config.model_id} · {PROVIDERS[config.provider]}'[:100],
                model_id=config.model_id, provider=config.provider, file_search=False)
            entry = Connection(str(session.session_id), profile, config.model_copy(deep=True), session.expires_at)
            self.connections[profile.id] = entry
            return ExternalModelConnection(profile=profile, expires_at=entry.expires_at)

    def profiles(self, session):
        with self.lock:
            self._prune()
            return {k: v.profile for k, v in self.connections.items() if v.owner == str(session.session_id)}

    def client(self, identifier, session):
        with self.lock:
            self._prune()
            entry = self.connections.get(identifier)
            if entry is None or entry.owner != str(session.session_id):
                raise CaseConflict('External model connection expired or is unavailable. Reconnect it from the model dropdown; no baseline model was used.')
            from app.enterprise_ai.external import ExternalModelClient
            return ExternalModelClient(entry.config)


def available_profiles(container, session=None):
    from app.chat_models import profiles
    configured = profiles(container.settings)
    if session is not None and hasattr(container, 'external_models'):
        configured.update(container.external_models.profiles(session))
    return configured
