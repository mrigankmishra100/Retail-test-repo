"""In-process RAG integration. No HTTP hop to the original retail backend.

Construction, login and app startup perform no DDL, seeding or cloud provisioning.
The existing DB pool/wallet, storage bucket and OCI identity are borrowed.
The prepared Distributor knowledge lives in separate DISTRIBUTOR_* tables.
"""
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from threading import BoundedSemaphore, Lock
from time import monotonic

from app.distributor_rag.domain.errors import AppError


def provider_settings(settings):
    from app.distributor_rag.providers.oci.settings import OCISettings
    values = dict(oci_region=settings.oci_region, oci_compartment_id=settings.oci_compartment_id,
        oci_namespace=settings.oci_namespace, oci_bucket=settings.oci_bucket_name,
        object_storage_prefix=settings.distributor_object_prefix,
        oci_llm_model_id=settings.distributor_llm_model_id,
        oci_llm_endpoint=settings.distributor_llm_endpoint,
        oci_llm_format=settings.distributor_llm_format,
        oci_embedding_model_id=settings.distributor_embedding_model_id,
        oci_embedding_endpoint=settings.distributor_embedding_endpoint,
        oci_embedding_dimensions=settings.distributor_embedding_dimensions,
        oci_vector_storage=settings.distributor_vector_storage)
    if any(value is None or value == '' for value in values.values()):
        raise AppError('Distributor model, embedding and shared OCI settings must be configured.', 'not_configured', 503)
    return OCISettings(_env_file=None, **values)


def identity(settings):
    import oci
    if settings.oci_auth_mode == 'resource_principal':
        return {'region': settings.oci_region}, oci.auth.signers.get_resource_principals_signer()
    if settings.oci_auth_mode == 'instance_principal':
        return {'region': settings.oci_region}, oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
    config = oci.config.from_file(str(Path(settings.oci_config_file).expanduser()), settings.oci_config_profile)
    config['region'] = settings.oci_region
    return config, oci.signer.Signer(config['tenancy'], config['user'], config['fingerprint'],
        config['key_file'], pass_phrase=config.get('pass_phrase'))


def screen(guardrails, content, stage, source):
    if not guardrails.enabled:
        return
    verdict = guardrails.evaluate_sync(content, 'distributor', stage=stage, source=source)
    if verdict.get('allowed') is not True:
        blocked = verdict.get('status') == 'BLOCKED'
        raise AppError('This content was blocked by the security policy.' if blocked else
            'Security checks are unavailable; no further AI processing was performed.',
            'security_blocked' if blocked else 'security_unavailable', 403 if blocked else 503)


class GuardedLLM:
    def __init__(self, delegate, guardrails):
        self.delegate, self.guardrails = delegate, guardrails

    def generate_response(self, system, user_message, context, config):
        screen(self.guardrails, context, 'retrieval', 'file_search')
        result = self.delegate.generate_response(system, user_message, context, config)
        screen(self.guardrails, result.text, 'output', 'model')
        return result


class DistributorService:
    def __init__(self, container, *, build=None, now=monotonic):
        self.container, self.build, self.now = container, build or self._build, now
        self._context = None
        self._lock, self._rate_lock = Lock(), Lock()
        self._slots, self._requests = BoundedSemaphore(2), deque()
        self._clients = []

    @contextmanager
    def admission(self):
        with self._rate_lock:
            now = self.now()
            while self._requests and self._requests[0] <= now - 60:
                self._requests.popleft()
            if len(self._requests) >= 30 or not self._slots.acquire(blocking=False):
                raise AppError('Distributor chat is busy. Try again later.', 'rate_limited', 429)
            self._requests.append(now)
        try:
            yield
        finally:
            self._slots.release()

    def context(self):
        if not self.container.settings.distributor_enabled:
            raise AppError('Distributor chat is not configured on this backend yet.', 'not_configured', 503)
        with self._lock:
            if self._context is None:
                self._context = self.build()
            return self._context

    def knowledge(self):
        from app.distributor_rag.services.knowledge import knowledge_status
        context = self.context()
        context.providers.ingestion.validate_configuration()
        status = knowledge_status(context)
        if status['ready']:
            document = context.repository.get_document(status['document_id'])
            if not context.providers.ingestion.get_ingestion_status(document).sync_complete:
                return {'ready': False, 'message': 'Distributor knowledge is not indexed for the configured embedding model.',
                        'distributors': status['distributors']}
        return status

    def ask(self, question):
        from app.distributor_rag.services.knowledge import answer_question
        if not isinstance(question, str) or not question.strip() or len(question) > 4000:
            raise AppError('Enter a question of 1–4000 characters.', 'invalid_question', 422)
        with self.admission():
            if not self.container.settings.distributor_enabled:
                raise AppError('Distributor chat is not configured on this backend yet.', 'not_configured', 503)
            screen(self.container.guardrails, question, 'input', 'user')
            return answer_question(self.context(), question.strip())

    def _build(self):
        import oci
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import NullPool
        from app.distributor_rag.core.config import Settings
        from app.distributor_rag.core.dependencies import build_container
        from app.distributor_rag.providers.factory import Providers
        from app.distributor_rag.providers.oci.repository import OracleRepository
        from app.distributor_rag.providers.oci.storage import OCIStorageProvider
        from app.distributor_rag.providers.oci.embeddings import OCIEmbeddings
        from app.distributor_rag.providers.oci.vector_search import OCIVectorSearchProvider
        from app.distributor_rag.providers.oci.ingestion import OCIIngestionProvider
        from app.distributor_rag.providers.oci.llm import OCILLMProvider

        settings = provider_settings(self.container.settings)
        graph_settings = Settings(_env_file=None, cloud_provider='oci', distributor_mode=True,
            object_storage_prefix=settings.object_storage_prefix)
        config, signer = identity(self.container.settings)
        clients, engine = [], None
        try:
            def client(client_type, **kwargs):
                instance = client_type(config, signer=signer, timeout=(10, 120),
                    retry_strategy=oci.retry.NoneRetryStrategy(), **kwargs)
                clients.append(instance)
                return instance
            storage = OCIStorageProvider(client(oci.object_storage.ObjectStorageClient), settings)
            embeddings = OCIEmbeddings(client(oci.generative_ai_inference.GenerativeAiInferenceClient,
                service_endpoint=settings.oci_embedding_endpoint), settings)
            # Reuse the existing authenticated DB pool; closing a DBAPI connection
            # returns it to that pool. No separate password or wallet in RAG settings.
            def acquire():
                connection = self.container.database._get_pool().acquire()
                connection.call_timeout = 30000
                return connection
            engine = create_engine('oracle+oracledb://', poolclass=NullPool, hide_parameters=True, creator=acquire)
            repository = OracleRepository(sessionmaker(engine, expire_on_commit=False))
            llm = OCILLMProvider(client(oci.generative_ai_inference.GenerativeAiInferenceClient,
                service_endpoint=settings.oci_llm_endpoint), settings, config, signer=signer)
            providers = Providers(GuardedLLM(llm, self.container.guardrails), storage,
                OCIVectorSearchProvider(engine, embeddings, settings), OCIIngestionProvider(storage, engine, embeddings, settings))
            context = build_container(graph_settings, providers, repository)
            context.engine = engine
            self._clients = clients
            return context
        except Exception:
            for instance in clients:
                instance.base_client.session.close()
            if engine is not None:
                engine.dispose()
            raise

    def close(self):
        with self._lock:
            if self._context is not None and self._context.engine is not None:
                self._context.engine.dispose()
            for client in self._clients:
                client.base_client.session.close()
            self._context, self._clients = None, []
