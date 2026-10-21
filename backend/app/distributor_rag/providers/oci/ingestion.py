import hashlib
import json
from array import array
from sqlalchemy import text, bindparam, Text
from app.distributor_rag.domain.models import IngestionState, Status
from app.distributor_rag.domain.errors import ProviderError
from app.distributor_rag.services.pdf_text import extract_pdf_text, PDFTextError
from app.distributor_rag.providers.oci.common import oci_operation, index_identity


def chunk_text(content, size, overlap):
    if overlap < 0 or overlap >= size:
        raise ValueError("Invalid chunk overlap")
    chunks = []
    start = 0
    while start < len(content):
        piece = content[start:start + size].strip()
        if piece:
            chunks.append(piece)
        if start + size >= len(content):
            break
        start += size - overlap
    return chunks

class OCIIngestionProvider:
    def __init__(self, storage, engine, embeddings, settings):
        self.storage, self.engine, self.embeddings, self.settings = storage, engine, embeddings, settings
        self.index_id = index_identity(settings)

    def validate_configuration(self):
        with oci_operation("index configuration"), self.engine.connect() as connection:
            found = connection.execute(text("SELECT COUNT(*) FROM user_tables WHERE table_name IN ('DISTRIBUTOR_CHUNKS', 'DISTRIBUTOR_INDEX')")).first()
            if not found or found[0] != 2:
                raise ProviderError("Distributor vector tables are not prepared. Ask the administrator to complete Distributor setup.")
            if getattr(self.settings, 'oci_vector_storage', 'native') == 'json':
                column = connection.execute(text("SELECT data_type FROM user_tab_columns WHERE table_name='DISTRIBUTOR_CHUNKS' AND column_name='EMBEDDING'")).scalar()
                if column != 'CLOB':
                    raise ProviderError('Distributor JSON storage does not match the prepared schema. No tables were changed.')

    def start_ingestion(self, document):
        params = {"document": document.id, "owner": document.owner_id, "index": self.index_id}
        with oci_operation("index lookup"), self.engine.connect() as connection:
            existing = connection.execute(text("SELECT digest FROM distributor_index WHERE document_id=:document AND owner_id=:owner AND index_id=:index"), params).scalar()
        if existing:
            return IngestionState(status=Status.PROCESSING, job_id=existing, sync_complete=True, message="Verifying document searchability.")
        content = self.storage.get_document(document.storage_key)
        try:
            extracted = extract_pdf_text(content) if document.content_type == "application/pdf" else content.decode("utf-8-sig")
        except (PDFTextError, UnicodeError) as exc:
            return IngestionState(status=Status.FAILED, message="Document text could not be extracted. Use a readable PDF or UTF-8 text.")
        chunks = chunk_text(extracted, self.settings.oci_chunk_chars, self.settings.oci_chunk_overlap)
        if not chunks or len(chunks) > 1500:
            return IngestionState(status=Status.FAILED, message="Document is empty or exceeds the 1500-chunk indexing limit.")
        vectors = self.embeddings.embed(chunks, "SEARCH_DOCUMENT")
        digest = hashlib.sha256(content + self.index_id.encode()).hexdigest()
        with oci_operation("indexing"), self.engine.begin() as connection:
            # Serialize retries for the same document, including across application processes.
            connection.execute(text("SELECT id FROM distributor_documents WHERE id=:document AND owner_id=:owner FOR UPDATE"),
                               {"document": document.id, "owner": document.owner_id}).scalar_one()
            connection.execute(text("DELETE FROM distributor_chunks WHERE document_id=:document AND owner_id=:owner AND index_id=:index"), params)
            for number, (chunk, vector) in enumerate(zip(chunks, vectors)):
                statement = text("""INSERT INTO distributor_chunks
                    (index_id, document_id, owner_id, chunk_id, content, embedding)
                    VALUES (:index, :document, :owner, :chunk_id, :content, :vector)""").bindparams(bindparam("content", type_=Text()))
                if getattr(self.settings, 'oci_vector_storage', 'native') == 'json':
                    # Oracle 19c: bind a real CLOB, not a VARCHAR2-limited string.
                    statement = statement.bindparams(bindparam('vector', type_=Text()))
                    encoded = json.dumps(vector, allow_nan=False, separators=(',', ':'))
                else:
                    encoded = array('f', vector)
                connection.execute(statement,
                    dict(params, chunk_id=hashlib.sha256((document.id + str(number) + chunk).encode()).hexdigest()[:24],
                         content=chunk, vector=encoded))
            connection.execute(text("DELETE FROM distributor_index WHERE document_id=:document AND owner_id=:owner AND index_id=:index"), params)
            connection.execute(text("""INSERT INTO distributor_index (index_id, document_id, owner_id, digest)
                VALUES (:index, :document, :owner, :digest)"""), dict(params, digest=digest))
        return IngestionState(status=Status.PROCESSING, job_id=digest, sync_complete=True, message="Indexed; verifying document searchability.")

    def get_ingestion_status(self, document):
        with oci_operation("index status"), self.engine.connect() as connection:
            digest = connection.execute(text("SELECT digest FROM distributor_index WHERE document_id=:document AND owner_id=:owner AND index_id=:index"),
                {"document": document.id, "owner": document.owner_id, "index": self.index_id}).scalar()
        return IngestionState(status=Status.PROCESSING, job_id=digest or document.ingestion_job_id,
                              sync_complete=bool(digest), message="Verifying document searchability." if digest else "Indexing document.")
