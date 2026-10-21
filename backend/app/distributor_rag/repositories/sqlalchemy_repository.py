from contextlib import contextmanager
from threading import RLock

from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app.distributor_rag.database.models import (
    ActiveKnowledgeRow,
    DocumentRow,
    IngestionLockRow,
    KnowledgeReleaseRow,
    QuestionAnswerRow,
)
from app.distributor_rag.domain.errors import AppError
from app.distributor_rag.domain.models import Document, KnowledgeRelease, QuestionAnswer, Status, utcnow


class SQLAlchemyRepository:
    def __init__(self, sessions: sessionmaker):
        self.sessions = sessions
        self.local_lock = RLock()

    def save_document_metadata(self, document: Document) -> Document:
        with self.sessions.begin() as session:
            session.add(DocumentRow(**document.model_dump()))
        return document

    def get_document(self, document_id: str) -> Document:
        with self.sessions() as session:
            row = session.get(DocumentRow, document_id)
            if row is None:
                raise AppError("Document not found.", "not_found", 404)
            return Document.model_validate(row)

    def update_document_status(
        self, document_id: str, status: Status, message: str, job_id: str | None = None
    ) -> Document:
        with self.sessions.begin() as session:
            row = session.get(DocumentRow, document_id)
            if row is None:
                raise AppError("Document not found.", "not_found", 404)
            row.ingestion_status, row.status_message, row.updated_at = status, message, utcnow()
            if job_id is not None:
                row.ingestion_job_id = job_id
            session.flush()
            return Document.model_validate(row)

    def list_uploaded_documents(self, limit: int = 100, offset: int = 0) -> list[Document]:
        with self.sessions() as session:
            return [
                Document.model_validate(row)
                for row in session.scalars(
                    select(DocumentRow)
                    .order_by(DocumentRow.created_at.desc(), DocumentRow.id)
                    .limit(limit)
                    .offset(offset)
                )
            ]

    def save_question_answer(self, answer: QuestionAnswer) -> QuestionAnswer:
        data = answer.model_dump(exclude={"sources"})
        with self.sessions.begin() as session:
            session.add(QuestionAnswerRow(**data, source_references=[s.model_dump() for s in answer.sources]))
        return answer

    def get_release_by_hash(self, digest: str) -> KnowledgeRelease | None:
        with self.sessions() as session:
            row = session.scalar(select(KnowledgeReleaseRow).where(KnowledgeReleaseRow.content_sha256 == digest))
            return KnowledgeRelease.model_validate(row) if row else None

    def register_release(self, release: KnowledgeRelease) -> KnowledgeRelease:
        with self.sessions.begin() as session:
            session.add(KnowledgeReleaseRow(**release.model_dump()))
        return release

    def get_active_release(self) -> KnowledgeRelease | None:
        with self.sessions() as session:
            active = session.get(ActiveKnowledgeRow, 1)
            return KnowledgeRelease.model_validate(session.get(KnowledgeReleaseRow, active.document_id)) if active else None

    def activate_release(self, document_id: str) -> KnowledgeRelease:
        with self.sessions.begin() as session:
            session.execute(select(IngestionLockRow).where(IngestionLockRow.id == 1).with_for_update()).scalar_one()
            release = session.get(KnowledgeReleaseRow, document_id)
            document = session.get(DocumentRow, document_id)
            if release is None or document is None or document.ingestion_status != Status.READY:
                raise AppError("Only an indexed, verified knowledge release can be activated.", "knowledge_not_ready", 409)
            active = session.get(ActiveKnowledgeRow, 1)
            if active:
                active.document_id = document_id
            else:
                session.add(ActiveKnowledgeRow(id=1, document_id=document_id))
            return KnowledgeRelease.model_validate(release)

    def get_question_answer_history(
        self, document_id: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[QuestionAnswer]:
        query = select(QuestionAnswerRow).order_by(QuestionAnswerRow.created_at.desc(), QuestionAnswerRow.id)
        if document_id is not None:
            query = query.where(QuestionAnswerRow.document_id == document_id)
        with self.sessions() as session:
            return [
                QuestionAnswer(
                    id=r.id,
                    document_id=r.document_id,
                    question=r.question,
                    answer=r.answer,
                    sources=r.source_references,
                    model_id=r.model_id,
                    latency_ms=r.latency_ms,
                    created_at=r.created_at,
                )
                for r in session.scalars(query.limit(limit).offset(offset))
            ]

    @contextmanager
    def ingestion_lock(self):
        # PostgreSQL SELECT FOR UPDATE serializes app workers. Local lock also supports SQLite tests.
        with self.local_lock, self.sessions.begin() as session:
            session.execute(
                select(IngestionLockRow).where(IngestionLockRow.id == 1).with_for_update()
            ).scalar_one()
            yield

    def healthcheck(self) -> None:
        with self.sessions() as session:
            session.execute(text("SELECT 1"))
            session.execute(select(DocumentRow.id).limit(1))
            session.execute(select(QuestionAnswerRow.id).limit(1))
            session.execute(select(IngestionLockRow).where(IngestionLockRow.id == 1)).scalar_one()
