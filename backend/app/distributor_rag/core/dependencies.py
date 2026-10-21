from dataclasses import dataclass
from typing import Any

from fastapi import Request

from app.distributor_rag.core.config import Settings
from app.distributor_rag.database.session import create_database
from app.distributor_rag.domain.models import InferenceConfig
from app.distributor_rag.interfaces.repository import Repository
from app.distributor_rag.providers.factory import Providers, create_providers
from app.distributor_rag.repositories.sqlalchemy_repository import SQLAlchemyRepository
from app.distributor_rag.services.documents import DocumentService
from app.distributor_rag.workflows.rag_graph import build_rag_graph


@dataclass
class Container:
    settings: Settings
    providers: Providers
    repository: Repository
    documents: DocumentService
    graph: Any
    engine: Any = None


def build_container(
    settings: Settings, providers: Providers | None = None, repository: Repository | None = None
) -> Container:
    if providers is None:
        providers = create_providers(settings)
        providers.ingestion.validate_configuration()
    engine = None
    if repository is None:
        if settings.cloud_provider == "oci":
            from app.distributor_rag.providers.oci.database import create_oracle_repository
            engine, repository = create_oracle_repository()
        else:
            engine, sessions = create_database(settings.database_url)
            repository = SQLAlchemyRepository(sessions)
    documents = DocumentService(
        providers.storage,
        providers.ingestion,
        providers.vector,
        repository,
        settings.object_storage_prefix if settings.cloud_provider == "oci" else settings.aws_s3_prefix,
        settings.max_upload_bytes,
        settings.ingestion_timeout_seconds,
    )
    graph = build_rag_graph(
        providers.llm,
        providers.vector,
        repository,
        InferenceConfig(max_tokens=settings.llm_max_tokens, temperature=settings.llm_temperature),
        settings.top_k,
        settings.context_max_chars,
        settings.max_question_chars,
        settings.distributor_mode,
    )
    return Container(settings, providers, repository, documents, graph, engine)


def get_container(request: Request) -> Container:
    return request.app.state.container
