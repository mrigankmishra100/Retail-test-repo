from pydantic import Field, model_validator
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.distributor_rag.core.config import ROOT

class OCISettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore", hide_input_in_errors=True)
    oci_region: str
    oci_compartment_id: str
    oci_namespace: str
    oci_bucket: str
    object_storage_prefix: str = "distributor/knowledge/"
    oci_config_file: str = "~/.oci/config"
    oci_profile: str = "DEFAULT"
    oci_llm_model_id: str
    oci_llm_endpoint: str
    oci_llm_format: str = "generic"
    oci_llm_serving_mode: str = "on_demand"
    oci_embedding_model_id: str
    oci_embedding_endpoint: str
    oci_embedding_serving_mode: str = "on_demand"
    oci_embedding_dimensions: int = Field(ge=1, le=16000)
    oci_vector_storage: Literal['native', 'json'] = 'native'
    oci_embedding_set_dimensions: bool = False
    oci_chunk_chars: int = Field(default=1800, ge=200, le=12000)
    oci_chunk_overlap: int = Field(default=200, ge=0)

    @model_validator(mode="after")
    def validate_oci(self):
        from urllib.parse import urlsplit
        prefix = self.object_storage_prefix.strip('/')
        if not prefix.startswith('distributor/') or '..' in prefix or '\\' in prefix:
            raise ValueError('Distributor objects require a dedicated distributor/ prefix')
        self.object_storage_prefix = prefix + '/'
        if self.oci_chunk_overlap >= self.oci_chunk_chars:
            raise ValueError("OCI chunk overlap must be less than chunk size")
        if self.oci_llm_format not in {"generic", "generic_astra", "cohere", "responses", "chat_completions"}:
            raise ValueError("Unsupported OCI LLM API format")
        for mode in (self.oci_llm_serving_mode, self.oci_embedding_serving_mode):
            if mode not in {"on_demand", "dedicated"}:
                raise ValueError("Unsupported OCI serving mode")
        for value in (self.oci_llm_endpoint, self.oci_embedding_endpoint):
            parsed = urlsplit(value)
            if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".oci.oraclecloud.com") or parsed.username or parsed.query or parsed.fragment:
                raise ValueError("Use a trusted HTTPS OCI service endpoint")
        return self
