import logging
from datetime import timezone
from pathlib import PurePosixPath
from uuid import uuid4

from app.distributor_rag.domain.errors import AppError, IngestionBusy, ProviderError
from app.distributor_rag.domain.models import Document, SearchFilter, Status, utcnow
from app.distributor_rag.interfaces.ingestion import DocumentIngestionProvider
from app.distributor_rag.interfaces.repository import Repository
from app.distributor_rag.interfaces.storage import ObjectStorageProvider
from app.distributor_rag.interfaces.vector_search import VectorSearchProvider

log = logging.getLogger(__name__)
CONTENT_TYPES = {".pdf": "application/pdf", ".txt": "text/plain", ".md": "text/markdown"}


def validate_document(filename: str, content: bytes, max_bytes: int) -> tuple[str, str]:
    filename = PurePosixPath(filename.replace("\\", "/")).name
    if not filename or len(filename) > 255 or any(ord(c) < 32 for c in filename):
        raise AppError("Provide a valid filename of at most 255 characters.", "invalid_filename")
    extension = PurePosixPath(filename).suffix.lower()
    if extension not in CONTENT_TYPES:
        raise AppError("Supported file types: PDF, TXT and Markdown.", "unsupported_type", 415)
    if not content:
        raise AppError("The document is empty.", "empty_document")
    if len(content) > max_bytes:
        raise AppError("The document exceeds the upload size limit.", "file_too_large", 413)
    if extension == ".pdf":
        if not content.startswith(b"%PDF-"):
            raise AppError("This file does not have a PDF signature.", "invalid_document")
    else:
        try:
            decoded = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise AppError("Text documents must use UTF-8 encoding.", "invalid_document") from exc
        if not decoded.strip() or "\x00" in decoded:
            raise AppError("Upload a nonempty text document.", "invalid_document")
    return filename, CONTENT_TYPES[extension]


class DocumentService:
    def __init__(
        self,
        storage: ObjectStorageProvider,
        ingestion: DocumentIngestionProvider,
        vector: VectorSearchProvider,
        repository: Repository,
        prefix: str,
        max_bytes: int,
        timeout_seconds: int,
    ):
        self.storage, self.ingestion, self.vector, self.repository = storage, ingestion, vector, repository
        self.prefix, self.max_bytes, self.timeout_seconds = prefix, max_bytes, timeout_seconds

    def upload(self, filename: str, content: bytes, document_id: str | None = None) -> Document:
        filename, content_type = validate_document(filename, content, self.max_bytes)
        identifier = document_id or str(uuid4())
        document = Document(
            id=identifier,
            original_filename=filename,
            content_type=content_type,
            storage_key=f"{self.prefix}{identifier}{PurePosixPath(filename).suffix.lower()}",
        )
        self.storage.upload_document(document, content)
        try:
            self.repository.save_document_metadata(document)
        except Exception:
            try:
                self.storage.delete_document(document.storage_key)
            except Exception:
                log.error("upload_compensation_failed", extra={"document_id": document.id})
            raise
        log.info("document_uploaded", extra={"document_id": document.id})
        # Metadata is durable before external ingestion. Transient failures leave an observable queued document.
        try:
            return self.refresh(document.id)
        except ProviderError:
            log.warning("ingestion_start_deferred", extra={"document_id": document.id})
            return self.repository.get_document(document.id)

    def refresh(self, document_id: str) -> Document:
        with self.repository.ingestion_lock():
            document = self.repository.get_document(document_id)
            if document.ingestion_status in {Status.READY, Status.FAILED}:
                return document
            created = (
                document.created_at.replace(tzinfo=timezone.utc)
                if document.created_at.tzinfo is None
                else document.created_at
            )
            if (utcnow() - created).total_seconds() > self.timeout_seconds:
                log.warning("ingestion_timeout", extra={"document_id": document.id})
                return self.repository.update_document_status(
                    document.id,
                    Status.FAILED,
                    "Indexing did not become searchable before the timeout. Check the index and upload again.",
                )
            if document.ingestion_job_id is None:
                try:
                    state = self.ingestion.start_ingestion(document)
                    log.info("ingestion_requested", extra={"document_id": document.id})
                except IngestionBusy:
                    return self.repository.update_document_status(
                        document.id,
                        Status.UPLOADED,
                        "Queued behind another indexing job. Keep this page open to retry automatically.",
                    )
            else:
                state = self.ingestion.get_ingestion_status(document)
                if state.sync_complete:
                    chunks = self.vector.retrieve_documents(
                        "Summarize this document",
                        SearchFilter(document_id=document.id, owner_id=document.owner_id),
                        1,
                    )
                    if chunks and all(c.document_id == document.id for c in chunks):
                        state.status, state.message = Status.READY, "Indexed and searchable."
            if state.status in {Status.READY, Status.FAILED}:
                log.info("ingestion_finished", extra={"document_id": document.id, "status": state.status})
            return self.repository.update_document_status(
                document.id, state.status, state.message, state.job_id
            )
