from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    app_name: str = "aws_rag"
    cloud_provider: Literal["aws", "oci"] = "aws"
    database_url: str = "postgresql+psycopg://rag:rag@localhost:5432/aws_rag"
    aws_region: str = ""
    aws_profile: str = ""
    aws_bedrock_model_id: str = ""
    aws_s3_bucket: str = ""
    object_storage_prefix: str = "documents/"
    aws_s3_prefix: str = "documents/"
    aws_knowledge_base_id: str = ""
    aws_kb_data_source_id: str = ""
    aws_kb_mode: Literal["conventional", "managed"] = "conventional"
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, ge=1, le=25 * 1024 * 1024)
    max_question_chars: int = Field(default=4000, ge=1, le=10000)
    top_k: int = Field(default=5, ge=1, le=20)
    context_max_chars: int = Field(default=24000, ge=1000, le=100000)
    llm_max_tokens: int = Field(default=1200, ge=1, le=8192)
    llm_temperature: float = Field(default=0, ge=0, le=1)
    ingestion_timeout_seconds: int = Field(default=3600, ge=60)
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:8080"]
    log_level: str = "INFO"
    distributor_mode: bool = True
    integration_api_keys: dict[str, SecretStr] = Field(default_factory=dict)
    integration_requests_per_minute: int = Field(default=30, ge=1, le=300)
    integration_max_concurrent: int = Field(default=2, ge=1, le=10)

    @model_validator(mode="after")
    def safe_prefix(self):
        self.object_storage_prefix = self.object_storage_prefix.strip("/") + "/"
        if self.object_storage_prefix == "/" or ".." in self.object_storage_prefix:
            raise ValueError("Use a nonempty dedicated object prefix without '..'.")
        self.aws_s3_prefix = self.aws_s3_prefix.strip("/") + "/"
        if self.aws_s3_prefix == "/" or ".." in self.aws_s3_prefix:
            raise ValueError("Use a nonempty dedicated document prefix without '..'.")
        return self

    def validate_provider(self) -> None:
        if self.cloud_provider == "aws":
            fields = (
                "aws_region",
                "aws_bedrock_model_id",
                "aws_s3_bucket",
                "aws_knowledge_base_id",
                "aws_kb_data_source_id",
            )
            missing = [name.upper() for name in fields if not getattr(self, name)]
            if missing:
                raise ValueError("Set required configuration: " + ", ".join(missing))


@lru_cache
def get_settings() -> Settings:
    return Settings()
