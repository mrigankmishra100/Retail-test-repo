from array import array
import json
import math
from sqlalchemy import text
from app.distributor_rag.domain.models import SearchChunk
from app.distributor_rag.domain.errors import ProviderError
from app.distributor_rag.providers.oci.common import oci_operation, index_identity


class OCIVectorSearchProvider:
    def __init__(self, engine, embeddings, settings):
        self.engine, self.embeddings = engine, embeddings
        self.index_id = index_identity(settings)
        self.storage = getattr(settings, 'oci_vector_storage', 'native')
        self.dimensions = settings.oci_embedding_dimensions

    def retrieve_documents(self, query, filters, top_k):
        if not 1 <= top_k <= 100:
            raise ValueError("top_k must be between 1 and 100")
        vector = self.embeddings.embed([query], "SEARCH_QUERY")[0]
        if self.storage == 'json':
            return self.retrieve_json(vector, filters, top_k)
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

    def retrieve_json(self, vector, filters, top_k):
        """Bounded exact cosine for small demo documents on Oracle 19c.

        Ownership, document and embedding-index filters are applied in SQL.
        No native-vector SQL, retail-table access, index creation or fallback
        that silently ignores invalid embeddings occurs during a query.
        """
        vector = unit_vector(vector, self.dimensions)
        ranked = []
        with oci_operation('JSON vector retrieval'), self.engine.connect() as connection:
            # IS JSON columns may be auto-decoded into Python lists on newer
            # Oracle clients. Serialize explicitly so the same bounded CLOB
            # read and numeric validation apply on both 19c and 26ai.
            rows = connection.execute(text("""SELECT chunk_id, document_id, content, page,
                JSON_SERIALIZE(embedding RETURNING CLOB) AS embedding
                FROM distributor_chunks
                WHERE document_id=:document AND owner_id=:owner AND index_id=:index
                AND ROWNUM <= :row_limit"""), {'document': filters.document_id,
                    'owner': filters.owner_id, 'index': self.index_id, 'row_limit': 1501}).mappings()
            for number, row in enumerate(rows):
                if number >= 1500:
                    raise ProviderError('Distributor demo search exceeds the 1500-chunk limit.')
                raw = bounded_text(row['embedding'], 1_000_000)
                embedded = unit_vector(json.loads(raw), self.dimensions)
                score = max(-1.0, min(1.0, math.fsum(a * b for a, b in zip(vector, embedded))))
                content = bounded_text(row['content'], 12000)
                ranked.append(SearchChunk(source_id=row['chunk_id'], document_id=row['document_id'],
                    text=content, score=score, metadata={'page': int(row['page'])} if row['page'] else {}))
        return sorted(ranked, key=lambda chunk: (-chunk.score, chunk.source_id))[:top_k]


def bounded_text(value, limit):
    if hasattr(value, 'read'):
        value = value.read(offset=1, amount=limit + 1)
    if not isinstance(value, str) or len(value) > limit:
        raise ProviderError('Distributor stored content exceeded its safe limit.')
    return value


def unit_vector(value, dimensions):
    if not isinstance(value, (list, tuple, array)) or len(value) != dimensions:
        raise ProviderError('Distributor embedding dimensions do not match the index.')
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in value):
        raise ProviderError('Distributor embeddings must contain finite numeric values.')
    norm = math.hypot(*value)
    if not norm or not math.isfinite(norm):
        raise ProviderError('Distributor embeddings must have a finite nonzero norm.')
    return [x / norm for x in value]
