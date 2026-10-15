"""Administrator-approved OCI Responses profiles; never accept client endpoints."""
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal


class ChatModelProfile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-zA-Z0-9_.-]{1,80}$')
    label: str = Field(min_length=1, max_length=100)
    model_id: str = Field(min_length=1, max_length=256, pattern=r'^[a-zA-Z0-9_.:-]+$')
    file_search: bool = False
    provider: Literal['oci', 'openai', 'gemini', 'anthropic'] = 'oci'
    input_usd_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    output_usd_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    cached_input_usd_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    cache_write_usd_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    file_search_usd_per_call: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    price_version: str | None = Field(default=None, max_length=100)

    @model_validator(mode='after')
    def price_reference(self):
        if any(v is not None for v in (self.input_usd_per_million, self.output_usd_per_million,
                                      self.cached_input_usd_per_million, self.cache_write_usd_per_million,
                                      self.file_search_usd_per_call)) and not self.price_version:
            raise ValueError('Model rates require a price_version (rate card and effective date)')
        return self


def profiles(settings):
    configured = list(settings.chat_model_profiles)
    if not configured and settings.enterprise_ai_model_id:
        configured = [ChatModelProfile(id='baseline', label='Configured OCI baseline',
            model_id=settings.enterprise_ai_model_id, file_search=settings.enterprise_ai_file_search_enabled)]
    return {profile.id: profile for profile in configured}
