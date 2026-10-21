from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Status(StrEnum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class TimestampedModel(BaseModel):
    @field_validator("created_at", "updated_at", check_fields=False)
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class Document(TimestampedModel):
    model_config = ConfigDict(from_attributes=True)
    id: str = Field(default_factory=lambda: str(uuid4()))
    owner_id: str = "single-user"
    original_filename: str
    storage_key: str
    content_type: str
    ingestion_status: Status = Status.UPLOADED
    ingestion_job_id: str | None = None
    status_message: str = "Waiting for indexing."
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class StoredObject(BaseModel):
    key: str
    reference: str


class KnowledgeRelease(TimestampedModel):
    model_config = ConfigDict(from_attributes=True)
    document_id: str
    content_sha256: str
    version: str
    demo_data: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class SearchChunk(BaseModel):
    source_id: str
    document_id: str
    text: str
    score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchFilter(BaseModel):
    document_id: str
    owner_id: str


class Source(BaseModel):
    source_id: str
    document_name: str
    excerpt: str
    page: int | None = None
    score: float | None = None


class InferenceConfig(BaseModel):
    max_tokens: int = Field(default=1200, ge=1, le=8192)
    temperature: float = Field(default=0.0, ge=0, le=1)


class Generation(BaseModel):
    text: str
    model_id: str
    usage: dict[str, int] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestionState(BaseModel):
    job_id: str | None = None
    status: Status
    message: str
    sync_complete: bool = False


class QuestionAnswer(TimestampedModel):
    model_config = ConfigDict(from_attributes=True)
    id: str = Field(default_factory=lambda: str(uuid4()))
    document_id: str
    question: str
    answer: str
    sources: list[Source] = Field(default_factory=list)
    model_id: str
    latency_ms: int
    created_at: datetime = Field(default_factory=utcnow)
