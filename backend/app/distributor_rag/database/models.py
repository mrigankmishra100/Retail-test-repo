from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class DocumentRow(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(1024), unique=True)
    content_type: Mapped[str] = mapped_column(String(100))
    ingestion_status: Mapped[str] = mapped_column(String(20), index=True)
    ingestion_job_id: Mapped[str | None] = mapped_column(String(128))
    status_message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class QuestionAnswerRow(Base):
    __tablename__ = "question_answers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="RESTRICT"))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    source_references: Mapped[list] = mapped_column(JSON)
    model_id: Mapped[str] = mapped_column(String(512))
    latency_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    __table_args__ = (Index("ix_question_answers_document_created", "document_id", "created_at"),)


class IngestionLockRow(Base):
    __tablename__ = "ingestion_locks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)


class KnowledgeReleaseRow(Base):
    __tablename__ = "knowledge_releases"
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="RESTRICT"), primary_key=True)
    content_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    version: Mapped[str] = mapped_column(String(100))
    demo_data: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ActiveKnowledgeRow(Base):
    __tablename__ = "active_knowledge"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_releases.document_id", ondelete="RESTRICT"))
