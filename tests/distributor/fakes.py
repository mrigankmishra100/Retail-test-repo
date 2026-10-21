from app.distributor_rag.domain.models import Generation, IngestionState, SearchChunk, Status, StoredObject


class MemoryStorage:
    def __init__(self):
        self.objects = {}

    def upload_document(self, document, content):
        self.objects[document.storage_key] = content
        return StoredObject(key=document.storage_key, reference="memory:" + document.storage_key)

    def get_document(self, key):
        return self.objects[key]

    def delete_document(self, key):
        self.objects.pop(key, None)

    def list_documents(self):
        return [StoredObject(key=k, reference="memory:" + k) for k in self.objects]


class FakeLLM:
    def __init__(self):
        self.calls = []
        self.answer = "The launch is in October. [source-1]"

    def generate_response(self, system, user_message, context, config):
        self.calls.append((system, user_message, context, config))
        return Generation(text=self.answer, model_id="test-model")


class FakeVector:
    def __init__(self):
        self.empty = False

    def retrieve_documents(self, query, filters, top_k):
        return (
            []
            if self.empty
            else [
                SearchChunk(
                    source_id="chunk-1",
                    document_id=filters.document_id,
                    text="The launch is in October.",
                    score=0.9,
                    metadata={"page": 1},
                )
            ]
        )


class FakeIngestion:
    def validate_configuration(self):
        pass

    def start_ingestion(self, document):
        return IngestionState(job_id="job-1", status=Status.PROCESSING, message="Processing")

    def get_ingestion_status(self, document):
        return IngestionState(
            job_id="job-1", status=Status.PROCESSING, message="Checking searchability", sync_complete=True
        )
