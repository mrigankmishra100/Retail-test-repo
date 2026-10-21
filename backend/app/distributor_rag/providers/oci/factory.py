import oci
from app.distributor_rag.providers.oci.database import create_oracle_engine
from app.distributor_rag.providers.factory import Providers
from app.distributor_rag.providers.oci.settings import OCISettings
from app.distributor_rag.providers.oci.common import load_identity
from app.distributor_rag.providers.oci.storage import OCIStorageProvider
from app.distributor_rag.providers.oci.embeddings import OCIEmbeddings
from app.distributor_rag.providers.oci.ingestion import OCIIngestionProvider
from app.distributor_rag.providers.oci.vector_search import OCIVectorSearchProvider
from app.distributor_rag.providers.oci.llm import OCILLMProvider


def create_oci_providers():
    settings = OCISettings()
    identity = load_identity(settings)
    storage = OCIStorageProvider(oci.object_storage.ObjectStorageClient(identity), settings)
    embeddings = OCIEmbeddings(oci.generative_ai_inference.GenerativeAiInferenceClient(identity,
        service_endpoint=settings.oci_embedding_endpoint, timeout=(10, 120)), settings)
    engine = create_oracle_engine()
    llm = OCILLMProvider(oci.generative_ai_inference.GenerativeAiInferenceClient(identity,
        service_endpoint=settings.oci_llm_endpoint, timeout=(10, 120)), settings, identity)
    return Providers(llm, storage, OCIVectorSearchProvider(engine, embeddings, settings),
                     OCIIngestionProvider(storage, engine, embeddings, settings))
