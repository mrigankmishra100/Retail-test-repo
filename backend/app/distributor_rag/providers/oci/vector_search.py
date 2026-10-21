from array import array
from sqlalchemy import text
from app.distributor_rag.domain.models import SearchChunk
from app.distributor_rag.providers.oci.common import oci_operation, index_identity


class OCIVectorSearchProvider:
    def __init__(self, engine, embeddings, settings):
        self.engine, self.embeddings = engine, embeddings
        self.index_id = index_identity(settings)

    def retrieve_documents(self, query, filters, top_k):
        if not 1 <= top_k <= 100:
            raise ValueError("top_k must be between 1 and 100")
        vector = self.embeddings.embed([query], "SEARCH_QUERY")[0]
        with oci_operation("vector retrieval"), self.engine.connect() as connection:
            rows = connection.execute(text("""
                SELECT chunk_id, document_id, content, page, 1 - distance AS score
                FROM (
                    SELECT chunk_id, document_id, content, page,
                           CASE WHEN index_id=:index THEN VECTOR_DISTANCE(embedding, :vector, COSINE) END AS distance
                    FROM distributor_chunks
                    WHERE document_id=:document AND owner_id=:owner AND index_id=:index
                    ORDER BY distance, chunk_id
                ) WHERE ROWNUM <= :top_k
            """), {"vector": array("f", vector), "document": filters.document_id,
                    "owner": filters.owner_id, "index": self.index_id, "top_k": top_k}).mappings().all()
            # Raw textual Oracle CLOB results may be driver LOBs; read before closing the connection.
            return [SearchChunk(source_id=row["chunk_id"], document_id=row["document_id"],
                    text=row["content"].read() if hasattr(row["content"], "read") else row["content"],
                    score=float(row["score"]), metadata={"page": int(row["page"])} if row["page"] else {})
                    for row in rows]
