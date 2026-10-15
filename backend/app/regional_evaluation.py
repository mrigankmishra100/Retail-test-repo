"""Request-scoped OCI regional evaluation; credentials are never persisted."""
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from typing import Literal
from app.enterprise_ai.responses_transport import normalize_endpoint, validate_genai_key
from app.chat_models import ChatModelProfile

REGIONAL_PROFILE = 'regional-evaluation'


class RegionalEvaluationModel(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True, str_strip_whitespace=True)
    label: str = Field(default='Regional model', min_length=1, max_length=100)
    region: str = Field(pattern=r'^[a-z]+(?:-[a-z]+)+-\d+$', max_length=60)
    model_id: str = Field(min_length=1, max_length=256, pattern=r'^[a-zA-Z0-9_.:-]+$')
    api_mode: Literal['responses', 'native_chat'] = 'responses'
    auth_mode: Literal['genai_api_key', 'oci'] = 'genai_api_key'
    project_id: str | None = Field(default=None, min_length=1, max_length=256)
    api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    compartment_id: str | None = Field(default=None, max_length=256)
    max_completion_tokens: int = Field(default=2048, ge=256, le=6000, strict=True)
    vector_store_ids: list[str] = Field(default_factory=list, max_length=5)

    @property
    def endpoint(self):
        base = f'https://inference.generativeai.{self.region}.oci.oraclecloud.com'
        return base if self.api_mode == 'native_chat' else base + '/openai/v1'

    @model_validator(mode='after')
    def validate_configuration(self):
        import re
        if self.api_mode == 'native_chat':
            if not re.fullmatch(r'ocid1\.compartment\.oc1\.\.[A-Za-z0-9]+', self.compartment_id or ''):
                raise ValueError('Native Chat requires a compartment OCID')
            match = re.fullmatch(r'ocid1\.generativeaimodel\.oc1\.([a-z0-9-]*)\.[A-Za-z0-9.]+', self.model_id)
            if not match or match.group(1) not in {'', self.region}:
                raise ValueError('Use a native Chat model OCID available in the selected region')
            if self.project_id or self.api_key or self.vector_store_ids:
                raise ValueError('Native Chat uses the server DEFAULT profile, without project, API key or File Search')
            return self
        if self.compartment_id:
            raise ValueError('Responses uses a project, not a compartment')
        normalize_endpoint(self.endpoint, self.project_id)
        if self.auth_mode == 'genai_api_key':
            validate_genai_key(self.api_key)
        elif self.api_key:
            raise ValueError('OCI profile authentication does not accept a GenAI API key')
        if self.model_id.startswith('ocid1.generativeaimodel.'):
            raise ValueError('Use a Responses model name or endpoint OCID, not a native Chat model OCID')
        if any(not re.fullmatch(r'[A-Za-z0-9_.-]{1,256}', identifier) for identifier in self.vector_store_ids):
            raise ValueError('Invalid vector store identifier')
        if self.model_id.startswith('ocid1.') and f'.{self.region}.' not in self.model_id:
            raise ValueError('Model OCID must belong to the selected region')
        return self

    def profile(self):
        return ChatModelProfile(id=REGIONAL_PROFILE, label=f'{self.label} · {self.region}',
                                model_id=self.model_id, file_search=self.api_mode == 'responses' and bool(self.vector_store_ids))

    def public_details(self):
        return {'id': REGIONAL_PROFILE, 'label': self.profile().label,
                'model_id': self.model_id, 'region': self.region, 'api_mode': self.api_mode}
