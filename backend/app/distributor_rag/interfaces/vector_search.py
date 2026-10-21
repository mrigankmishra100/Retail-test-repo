from typing import Protocol

from app.distributor_rag.domain.models import SearchChunk, SearchFilter


class VectorSearchProvider(Protocol):
    def retrieve_documents(self, query: str, filters: SearchFilter, top_k: int) -> list[SearchChunk]: ...
