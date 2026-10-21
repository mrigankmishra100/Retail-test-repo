from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import router
from app.config import Settings
from app.distributor_api import service
from app.distributor_rag.core.config import Settings as RAGSettings
from app.distributor_rag.core.dependencies import build_container
from app.distributor_rag.domain.errors import AppError
from app.distributor_rag.domain.models import Document, KnowledgeRelease, Status
from app.distributor_rag.providers.factory import Providers
from app.distributor_rag.providers.oci.database_models import Base, IngestionLockRow
from app.distributor_rag.providers.oci.repository import OracleRepository
from app.services.distributor import DistributorService, GuardedLLM, provider_settings
from app.services.sessions import SessionService
from app.session_api import get_session_service
from tests.distributor.fakes import FakeIngestion, FakeLLM, FakeVector, MemoryStorage


class Cache:
    def __init__(self): self.values = {}
    def set_state(self, key, value, **kwargs): self.values[key] = value; return True
    def get_state(self, key): return self.values.get(key)
    def delete_state(self, key): self.values.pop(key, None)


@pytest.fixture
def harness():
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions.begin() as session:
        session.add(IngestionLockRow(id=1))
    repository = OracleRepository(sessions)
    document = repository.save_document_metadata(Document(original_filename='Distributor_DEMO.md',
        storage_key='distributor/knowledge/demo.md', content_type='text/markdown', ingestion_status=Status.READY))
    repository.register_release(KnowledgeRelease(document_id=document.id, content_sha256='a'*64, version='demo-v1', demo_data=True))
    repository.activate_release(document.id)
    providers = Providers(FakeLLM(), MemoryStorage(), FakeVector(), FakeIngestion())
    context = build_container(RAGSettings(_env_file=None, cloud_provider='oci', distributor_mode=True), providers, repository)
    container = SimpleNamespace(settings=Settings(_env_file=None, distributor_enabled=True), guardrails=SimpleNamespace(enabled=False))
    agent = DistributorService(container, build=lambda: context)
    identities = SessionService(Cache(), Mock(fetch_all=lambda *args: [{'supplier_id':'SUP001'}]), distributor_enabled=True)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session_service] = lambda: identities
    app.dependency_overrides[service] = lambda: agent
    with TestClient(app) as client:
        yield client, agent, context, identities
    engine.dispose()


def login(client, role='distributor'):
    body = {'role':role}
    if role == 'supplier': body['supplier_id'] = 'SUP001'
    response = client.post('/sessions', json=body)
    assert response.status_code == 201, response.text
    return {'Authorization': 'Bearer ' + response.json()['session_token']}


def test_three_server_managed_roles_and_distributor_answer(harness):
    client, agent, context, identities = harness
    for role in ['retail-manager', 'supplier', 'distributor']:
        headers = login(client, role)
        assert client.get('/sessions/me', headers=headers).json()['role'] == role
    assert client.get('/distributor/knowledge').status_code == 401
    headers = login(client)
    assert client.get('/distributor/knowledge', headers=headers).json()['ready'] is True
    answer = client.post('/distributor/questions/ask', headers=headers, json={'question':'When is the launch?'})
    assert answer.status_code == 200, answer.text
    assert answer.headers['cache-control'] == 'no-store'
    assert answer.json()['sources'][0]['document_name'] == 'Distributor_DEMO.md'
    assert answer.json()['demo_data'] is True
    assert context.providers.llm.calls
    assert client.delete('/sessions/me', headers=headers).status_code == 204
    assert client.get('/distributor/knowledge', headers=headers).status_code == 401


@pytest.mark.parametrize('path,method,body', [
    ('/inventory/risks','GET',None), ('/cases','GET',None),
    ('/registry/agents','GET',None), ('/chat/models','GET',None),
    ('/supplier/policy','GET',None), ('/memory/approved','GET',None),
    ('/chat','POST',{'message':'Show stock', 'intent':'conversation'}),
])
def test_distributor_cannot_use_business_endpoints(harness, path, method, body):
    client, *_ = harness
    response = client.request(method, path, headers=login(client), **({'json':body} if body else {}))
    assert response.status_code == 403, (path, response.text)


@pytest.mark.parametrize('role', ['retail-manager', 'supplier'])
def test_existing_roles_do_not_gain_distributor_access(harness, role):
    client, *_ = harness
    assert client.get('/distributor/knowledge', headers=login(client, role)).status_code == 403


@pytest.mark.parametrize('body', [{'question':''}, {'question':'x'*4001}, {'question':'Hi', 'document_id':'other'}, {'question':'Hi', 'role':'retail-manager'}])
def test_question_contract_cannot_override_document_or_identity(harness, body):
    client, *_ = harness
    assert client.post('/distributor/questions/ask', headers=login(client), json=body).status_code == 422


def test_distributor_cannot_claim_supplier_identity(harness):
    client, *_ = harness
    assert client.post('/sessions', json={'role':'distributor','supplier_id':'SUP001'}).status_code == 422


def test_unconfigured_existing_backend_preserves_two_roles():
    identities = SessionService(Cache(), Mock(), distributor_enabled=False)
    identities.create('retail-manager')
    with pytest.raises(PermissionError, match='not configured'):
        identities.create('distributor')


def test_failure_is_sanitized_and_never_looks_successful(harness):
    client, agent, *_ = harness
    agent.build = Mock(side_effect=RuntimeError('DB_PASSWORD=private-value'))
    response = client.get('/distributor/knowledge', headers=login(client))
    assert response.status_code == 503 and 'private-value' not in response.text


def test_no_model_without_knowledge_or_evidence(harness):
    client, agent, context, *_ = harness
    context.providers.vector.empty = True
    response = client.post('/distributor/questions/ask', headers=login(client), json={'question':'Unknown facts?'})
    assert response.json()['answer_status'] == 'insufficient_information'
    assert not context.providers.llm.calls


def test_bounded_requests_and_no_hidden_retry(harness):
    _, agent, context, *_ = harness
    context.providers.vector.empty = True
    for _ in range(30): agent.ask('Facts?')
    with pytest.raises(AppError) as failure: agent.ask('Facts?')
    assert failure.value.status_code == 429
    assert len(agent._requests) == 30


def test_input_guardrail_failure_prevents_rag_initialization(harness):
    _, agent, *_ = harness
    agent.container.guardrails = Mock(enabled=True)
    agent.container.guardrails.evaluate_sync.return_value = {'allowed':False,'status':'FAILED'}
    agent.build = Mock()
    with pytest.raises(AppError) as failure: agent.ask('Question')
    assert failure.value.status_code == 503
    agent.build.assert_not_called()


def test_retrieved_and_generated_content_use_existing_guardrails():
    guardrails = Mock(enabled=True)
    guardrails.evaluate_sync.return_value = {'allowed':True,'status':'PASSED'}
    llm = FakeLLM()
    guarded = GuardedLLM(llm, guardrails)
    guarded.generate_response('system','question','evidence',None)
    assert [call.kwargs['source'] for call in guardrails.evaluate_sync.call_args_list] == ['file_search','model']
    guardrails.evaluate_sync.return_value = {'allowed':False,'status':'BLOCKED'}
    with pytest.raises(AppError): guarded.generate_response('system','question','evidence',None)
    assert len(llm.calls) == 1


def test_only_namespaced_tables_and_no_ddl_on_construction():
    assert all(name.startswith('distributor_') for name in Base.metadata.tables)
    container = SimpleNamespace(settings=Settings(_env_file=None))
    builder = Mock()
    agent = DistributorService(container, build=builder)
    agent.close()
    builder.assert_not_called()


def test_explicit_model_mapping_does_not_adopt_retail_file_search():
    settings = Settings(_env_file=None, distributor_enabled=True,
        enterprise_ai_model_id='retail-model', enterprise_ai_vector_store_ids=['retail-store'])
    with pytest.raises(AppError, match='must be configured'): provider_settings(settings)


def test_resource_principal_identity_never_loads_user_key(monkeypatch):
    import oci
    from app.services.distributor import identity
    signer = Mock()
    monkeypatch.setattr(oci.auth.signers, 'get_resource_principals_signer', lambda: signer)
    monkeypatch.setattr(oci.config, 'from_file', Mock(side_effect=AssertionError('User keys must not be loaded')))
    config, result = identity(Settings(_env_file=None, oci_auth_mode='resource_principal', oci_region='us-ashburn-1'))
    assert result is signer and config == {'region':'us-ashburn-1'}


@pytest.mark.parametrize('override', [{}, {'distributor_object_prefix':'retail/'},
    {'distributor_llm_endpoint':'https://example.com/model'}])
def test_private_model_mapping_and_isolated_object_prefix(override):
    from pydantic import ValidationError
    values = dict(distributor_enabled=True, oci_region='us-ashburn-1',
        oci_compartment_id='fixture-compartment', oci_namespace='fixture-namespace',
        oci_bucket_name='existing-bucket', distributor_llm_model_id='fixture-answer-model',
        distributor_llm_endpoint='https://inference.generativeai.us-ashburn-1.oci.oraclecloud.com',
        distributor_embedding_model_id='fixture-embedding-model',
        distributor_embedding_endpoint='https://inference.generativeai.us-ashburn-1.oci.oraclecloud.com',
        distributor_embedding_dimensions=1024)
    settings = Settings(_env_file=None, **{**values, **override})
    if override:
        with pytest.raises(ValidationError): provider_settings(settings)
    else:
        mapped = provider_settings(settings)
        assert mapped.oci_bucket == 'existing-bucket'
        assert mapped.oci_embedding_dimensions == 1024
        assert mapped.object_storage_prefix == 'distributor/knowledge/'


def test_setup_plan_is_read_only_and_existing_tables_block_adoption(monkeypatch):
    from app import distributor_setup
    inspector, initialize = Mock(), Mock()
    inspector.get_table_names.return_value = ['ITEMS', 'SALES_HISTORY']
    monkeypatch.setattr(distributor_setup, 'inspect', lambda engine: inspector)
    monkeypatch.setattr(distributor_setup, 'initialize_schema', initialize)
    result = distributor_setup.prepare(Mock())
    assert len(result['tables']) == 7 and all(name.startswith('distributor_') for name in result['tables'])
    initialize.assert_not_called()
    distributor_setup.prepare(Mock(), apply=True)
    initialize.assert_called_once()
    inspector.get_table_names.return_value = ['DISTRIBUTOR_DOCUMENTS']
    with pytest.raises(ValueError, match='Refusing to adopt'):
        distributor_setup.prepare(Mock(), apply=True)
    initialize.assert_called_once()


def test_19c_setup_plan_and_apply_forward_only_explicit_json_mode(monkeypatch):
    from app import distributor_setup
    inspector, initialize, engine = Mock(), Mock(), Mock()
    inspector.get_table_names.return_value = ['ITEMS', 'SALES_HISTORY', 'DELIVERY_CONNECTIONS']
    monkeypatch.setattr(distributor_setup, 'inspect', lambda engine: inspector)
    monkeypatch.setattr(distributor_setup, 'initialize_schema', initialize)
    plan = distributor_setup.prepare(engine, vector_storage='json')
    assert plan['vector_storage'] == 'json' and plan['retail_tables_changed'] is False
    assert len(plan['tables']) == 7 and not plan['already_present']
    initialize.assert_not_called()
    distributor_setup.prepare(engine, apply=True, vector_storage='json')
    initialize.assert_called_once_with(engine, vector_storage='json')


def test_19c_config_mode_survives_private_json_and_provider_mapping(monkeypatch):
    import json
    endpoint = 'https://inference.generativeai.us-ashburn-1.oci.oraclecloud.com'
    monkeypatch.setenv('APP_CONFIG_JSON', json.dumps({
        'DISTRIBUTOR_ENABLED': True, 'DISTRIBUTOR_VECTOR_STORAGE': 'json',
        'OCI_REGION': 'us-ashburn-1', 'OCI_COMPARTMENT_ID': 'fixture-compartment',
        'OCI_NAMESPACE': 'fixture-namespace', 'OCI_BUCKET_NAME': 'existing-bucket',
        'DISTRIBUTOR_LLM_MODEL_ID': 'google.gemini-2.5-flash', 'DISTRIBUTOR_LLM_ENDPOINT': endpoint,
        'DISTRIBUTOR_EMBEDDING_MODEL_ID': 'cohere.embed-v4.0', 'DISTRIBUTOR_EMBEDDING_ENDPOINT': endpoint,
        'DISTRIBUTOR_EMBEDDING_DIMENSIONS': 1536}))
    settings = Settings(_env_file=None)
    mapped = provider_settings(settings)
    assert settings.distributor_vector_storage == mapped.oci_vector_storage == 'json'
    assert mapped.oci_embedding_dimensions == 1536


def test_embedding_change_is_not_reported_as_ready(harness):
    _, agent, context, *_ = harness
    from app.distributor_rag.domain.models import IngestionState
    context.providers.ingestion.get_ingestion_status = lambda doc: IngestionState(status=Status.PROCESSING, sync_complete=False, message='Index missing')
    assert agent.knowledge()['ready'] is False


def test_runtime_json_accepts_distributor_settings_without_overwriting_retail(monkeypatch):
    import json
    endpoint = 'https://inference.generativeai.ap-hyderabad-1.oci.oraclecloud.com/20251112/hostedApplications/ocid1.generativeaihostedapplication.oc1.ap-hyderabad-1.fixture/actions/invoke'
    monkeypatch.setenv('APP_CONFIG_JSON', json.dumps({'DISTRIBUTOR_ENABLED':True,
        'DISTRIBUTOR_EMBEDDING_DIMENSIONS':1024, 'AGENT_ENDPOINTS':{'retail':endpoint},
        'AGENT_SERVICE_KEYS':{'retail':'x'*64}}))
    config = Settings(_env_file=None)
    assert config.distributor_enabled and config.distributor_embedding_dimensions == 1024
    assert config.agent_endpoints == {'retail':endpoint}


def test_shipped_frontend_forwards_distributor_routes_and_sessions(harness, tmp_path, monkeypatch):
    import httpx
    from app.frontend import FrontendSettings, create_frontend
    backend, *_ = harness
    (tmp_path / 'index.html').write_text('<html>fixture</html>', encoding='utf-8')
    frontend = create_frontend(FrontendSettings(backend_invoke_base='http://backend',
        backend_auth_mode='none', backend_api_contract='integrated',
        frontend_allow_anonymous=True, app_env='development', frontend_static_dir=str(tmp_path)))
    original_client = httpx.AsyncClient
    monkeypatch.setattr('app.frontend.httpx.AsyncClient', lambda **kwargs:
        original_client(transport=httpx.ASGITransport(app=backend.app), **kwargs))
    with TestClient(frontend) as client:
        assert client.get('/api/distributor/knowledge').status_code == 401
        for role in ['retail-manager', 'supplier', 'distributor']:
            body = {'role': role}
            if role == 'supplier': body['supplier_id'] = 'SUP001'
            response = client.post('/api/sessions', json=body)
            assert response.status_code == 201, response.text
            headers = {'Authorization': 'Bearer ' + response.json()['session_token']}
            knowledge = client.get('/api/distributor/knowledge', headers=headers)
            answer = client.post('/api/distributor/questions/ask', headers=headers, json={'question':'When is the launch?'})
            if role != 'distributor':
                assert knowledge.status_code == answer.status_code == 403
                continue
            assert knowledge.status_code == answer.status_code == 200
            assert knowledge.json()['ready'] is True
            assert answer.json()['sources'][0]['document_name'] == 'Distributor_DEMO.md'
            assert answer.headers['cache-control'] == 'no-store'
            assert client.get('/api/inventory/risks', headers=headers).status_code == 403
            assert client.delete('/api/sessions/me', headers=headers).status_code == 204
            assert client.get('/api/distributor/knowledge', headers=headers).status_code == 401


@pytest.mark.parametrize('method,path,query', [
    ('POST', 'distributor/knowledge', ''),
    ('GET', 'distributor/questions/ask', ''),
    ('GET', 'distributor/questions', ''),
    ('POST', 'distributor/documents', ''),
    ('GET', 'distributor/admin', ''),
    ('GET', 'distributor/knowledge', 'document_id=other'),
])
def test_distributor_proxy_does_not_expose_other_upstream_routes(method, path, query):
    from fastapi import HTTPException
    from app.proxy_routes import integrated_target
    with pytest.raises(HTTPException) as rejected:
        integrated_target(method, path, query)
    assert rejected.value.status_code in {400, 404}
