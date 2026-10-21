from app.distributor_rag.domain.errors import AppError
from app.distributor_rag.domain.models import Status
from app.distributor_rag.services.distributors import DISTRIBUTORS


def active_knowledge(container):
    release = container.repository.get_active_release()
    if release is None:
        raise AppError("The distributor knowledge document has not been activated yet.", "knowledge_not_ready", 503)
    document = container.repository.get_document(release.document_id)
    if document.ingestion_status != Status.READY:
        raise AppError("The distributor knowledge document is not ready.", "knowledge_not_ready", 503)
    return release, document


def knowledge_status(container):
    try:
        release, document = active_knowledge(container)
    except AppError as exc:
        if exc.code != "knowledge_not_ready":
            raise
        return {"ready": False, "message": exc.message, "distributors": DISTRIBUTORS}
    return {"ready": True, "document_id": document.id, "document_name": document.original_filename,
            "version": release.version, "demo_data": release.demo_data,
            "updated_at": release.created_at, "distributors": DISTRIBUTORS}


def answer_question(container, question: str, requested_document_id: str | None = None):
    release, document = active_knowledge(container)
    if requested_document_id and requested_document_id != document.id:
        raise AppError("Questions must use the active distributor knowledge document.", "invalid_document", 400)
    answer = container.graph.invoke({"document_id": document.id, "question": question,
                                     "demo_data": release.demo_data})["result"]
    return {**answer.model_dump(), "knowledge_version": release.version,
            "demo_data": release.demo_data,
            "answer_status": "answered" if answer.sources else "insufficient_information"}
