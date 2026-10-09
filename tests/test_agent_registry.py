"""Offline registry coverage: session API, Oracle operations and signed routing."""

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from app.agents.remote_contract import decode_transport, transport_signature, wrap_transport
from app.config import Settings
from app.dependencies import ApplicationContainer
from app.http_contract import install_http_contract
from app.oci.cache import OCICacheClient
from app.oci.database import OracleDatabaseClient, READ_OPERATIONS, WRITE_OPERATIONS
from app.proxy_routes import integrated_target
from app.registry_api import registry_service, router
from app.services.agent_registry import AgentRegistration, AgentRegistryService
from app.services.readiness import ReadinessService
from app.services.sessions import SessionContext, SessionService
from app.session_api import get_session_service


BASE = "https://inference.generativeai.ap-hyderabad-1.oci.oraclecloud.com/20251112/hostedApplications/"
FALLBACK = BASE + "ocid1.generativeaihostedapplication.oc1.ap-hyderabad-1.fallback/actions/invoke"
REGISTERED = BASE + "ocid1.generativeaihostedapplication.oc1.ap-hyderabad-1.registered/actions/invoke"


def db_error(code):
    return RuntimeError(SimpleNamespace(code=code))


def registration(**changes):
    return {"name": "Retail agent", "agent_type": "RETAIL", "description": "POC agent",
            "endpoint_url": REGISTERED, "active": False, **changes}


class MemoryRegistryDatabase:
    """Emulate rows and Oracle uniqueness, retaining the real SQL bind allowlist."""
    def __init__(self):
        self.rows = {}
        self.calls = []

    def fetch_all(self, operation, parameters=None):
        self.calls.append((operation, parameters))
        if operation == "supplier_identity":
            return [{"supplier_id": parameters["supplier_id"]}]
        OracleDatabaseClient._resolve_operation(READ_OPERATIONS, operation, parameters)
        if operation == "registry_agent":
            row = self.rows.get(parameters["agent_id"])
            return [dict(row)] if row else []
        if operation == "registry_active_endpoint":
            return [{"endpoint_url": row["endpoint_url"]} for row in self.rows.values()
                    if row["active"] and row["agent_type"] == parameters["agent_type"]][:2]
        if operation == "registry_agents":
            rows = sorted(self.rows.values(), key=lambda row: (row["created_at"], row["agent_id"]), reverse=True)
            return rows[parameters["offset"]:parameters["offset"] + parameters["page_size"]]
        raise AssertionError(operation)

    def execute(self, operation, parameters):
        self.calls.append((operation, parameters))
        OracleDatabaseClient._resolve_operation(WRITE_OPERATIONS, operation, parameters)
        agent_id = parameters["agent_id"]
        if operation == "registry_set_active" and agent_id not in self.rows:
            return False
        now = datetime.now(timezone.utc)
        row = ({**parameters, "created_at": now} if operation == "registry_create_agent"
               else {**self.rows[agent_id], **parameters})
        if row["active"] and row["agent_type"] != "CUSTOM":
            if any(existing["agent_id"] != agent_id and existing["active"]
                   and existing["agent_type"] == row["agent_type"] for existing in self.rows.values()):
                raise db_error(1)
        self.rows[agent_id] = {**row, "updated_at": now}
        return True


@pytest.fixture
def api():
    database = MemoryRegistryDatabase()
    sessions = SessionService(OCICacheClient(allow_memory=True), database)
    application = FastAPI()
    install_http_contract(application)
    application.include_router(router)
    application.dependency_overrides[registry_service] = lambda: AgentRegistryService(database)
    application.dependency_overrides[get_session_service] = lambda: sessions
    with TestClient(application) as client:
        yield client, database, sessions


def auth(sessions, role="retail-manager"):
    session = sessions.create(role, "SUP001" if role == "supplier" else None)
    return {"Authorization": "Bearer " + session["session_token"]}


@pytest.mark.parametrize("path,method", [("/registry/agents", "GET"), ("/registry/agents", "POST"),
    (f"/registry/agents/{uuid4()}/activate", "POST"), (f"/registry/agents/{uuid4()}/deactivate", "POST")])
def test_registry_requires_existing_application_session(api, path, method):
    client, database, _ = api
    for headers in ({}, {"Authorization": "Bearer " + "x" * 43}):
        response = client.request(method, path, headers=headers, json=registration() if path == "/registry/agents" and method == "POST" else None)
        assert response.status_code == 401
    assert database.calls == []


@pytest.mark.parametrize("role", ["retail-manager", "supplier"])
def test_both_roles_can_register_list_activate_and_deactivate(api, role):
    client, database, sessions = api
    headers = auth(sessions, role)
    response = client.post("/registry/agents", headers=headers, json=registration(name="  POC agent  "))
    assert response.status_code == 201
    agent = response.json()
    assert agent["name"] == "POC agent" and agent["active"] is False
    assert agent["created_at"] and agent["updated_at"]
    assert len(database.rows) == 1
    assert client.get("/registry/agents", headers=headers).json() == {"items": [agent], "has_more": False}
    for operation, active in (("activate", True), ("activate", True), ("deactivate", False), ("deactivate", False)):
        response = client.post(f'/registry/agents/{agent["agent_id"]}/{operation}', headers=headers)
        assert response.status_code == 200
        assert response.json()["active"] is active


@pytest.mark.parametrize("agent_type", ["RETAIL", "SUPPLIER"])
def test_single_active_destination_and_explicit_switch(api, agent_type):
    client, _, sessions = api
    headers = auth(sessions)
    first = client.post("/registry/agents", headers=headers, json=registration(agent_type=agent_type, active=True)).json()
    conflict = client.post("/registry/agents", headers=headers, json=registration(agent_type=agent_type, active=True))
    assert conflict.status_code == 409
    second = client.post("/registry/agents", headers=headers, json=registration(agent_type=agent_type)).json()
    assert client.post(f'/registry/agents/{second["agent_id"]}/activate', headers=headers).status_code == 409
    assert client.post(f'/registry/agents/{first["agent_id"]}/deactivate', headers=headers).status_code == 200
    assert client.post(f'/registry/agents/{second["agent_id"]}/activate', headers=headers).status_code == 200


def test_custom_agents_are_stored_only_and_results_are_paginated(api):
    client, database, sessions = api
    headers = auth(sessions)
    for index in range(3):
        assert client.post("/registry/agents", headers=headers,
                           json=registration(name=str(index), agent_type="CUSTOM", active=True)).status_code == 201
    first = client.get("/registry/agents?limit=2", headers=headers).json()
    second = client.get("/registry/agents?limit=2&offset=2", headers=headers).json()
    assert [agent["name"] for agent in first["items"] + second["items"]] == ["2", "1", "0"]
    assert first["has_more"] is True and second["has_more"] is False
    service = AgentRegistryService(database)
    assert service.active_endpoint("retail") is None
    assert service.active_endpoint("supplier") is None
    with pytest.raises(ValueError):
        service.active_endpoint("custom")


@pytest.mark.parametrize("url", [REGISTERED.replace("https:", "http:"), "https://127.0.0.1/actions/invoke",
    REGISTERED.replace("oci.oraclecloud.com", "oci.oraclecloud.com.attacker.example"),
    REGISTERED.replace("https://", "https://user:password@"), REGISTERED + "?secret=value",
    REGISTERED + "#fragment", REGISTERED + "/internal/agent/invoke", REGISTERED + "/../other",
    REGISTERED.replace(".com/", ".com:443/"), REGISTERED.replace("https://", "https://\n"),
    REGISTERED.replace("inference", "infer\tence"), " " + REGISTERED, REGISTERED + "?", REGISTERED + "#"])
def test_registry_rejects_non_invoke_urls(api, url):
    client, database, sessions = api
    response = client.post("/registry/agents", headers=auth(sessions), json=registration(endpoint_url=url))
    assert response.status_code == 422
    assert not database.rows


@pytest.mark.parametrize("changes", [{"name": "   "}, {"name": "n" * 121}, {"description": "d" * 1001},
    {"agent_type": "ADMIN"}, {"active": "true"}, {"service_key": "do-not-accept-keys"},
    {"oci_auth_config": "NO_AUTH_CONFIG"}])
def test_registration_bounds_and_extra_fields(api, changes):
    client, _, sessions = api
    assert client.post("/registry/agents", headers=auth(sessions), json=registration(**changes)).status_code == 422


def test_unknown_agent_bad_ids_and_pagination_bounds(api):
    client, _, sessions = api
    headers = auth(sessions)
    assert client.post(f"/registry/agents/{uuid4()}/activate", headers=headers).status_code == 404
    assert client.post("/registry/agents/not-a-uuid/deactivate", headers=headers).status_code == 422
    for query in ("limit=0", "limit=101", "offset=-1", "offset=10001"):
        assert client.get("/registry/agents?" + query, headers=headers).status_code == 422


def test_missing_migration_has_fallback_but_registry_api_reports_unavailable(api, monkeypatch):
    client, database, sessions = api
    headers = auth(sessions)
    monkeypatch.setattr(database, "fetch_all", Mock(side_effect=db_error(942)))
    assert AgentRegistryService(database).active_endpoint("retail") is None
    assert client.get("/registry/agents", headers=headers).status_code == 503
    monkeypatch.setattr(database, "execute", Mock(side_effect=db_error(942)))
    assert client.post("/registry/agents", headers=headers, json=registration()).status_code == 503


@pytest.mark.parametrize("code", [1017, 3113, 904])
def test_registry_failure_does_not_silently_choose_another_agent(code):
    database = SimpleNamespace(fetch_all=Mock(side_effect=db_error(code)))
    with pytest.raises(RuntimeError):
        AgentRegistryService(database).active_endpoint("retail")


def test_db_modified_urls_and_multiple_destinations_are_rejected():
    database = SimpleNamespace(fetch_all=Mock(return_value=[{"endpoint_url": "http://localhost"}]))
    with pytest.raises(ValueError):
        AgentRegistryService(database).active_endpoint("retail")
    database.fetch_all.return_value = [{"endpoint_url": REGISTERED}] * 2
    with pytest.raises(RuntimeError):
        AgentRegistryService(database).active_endpoint("retail")
    assert AgentRegistration(**registration(endpoint_url=REGISTERED + "/")).endpoint_url == REGISTERED


@pytest.mark.parametrize("audience,role", [("retail", "retail-manager"), ("supplier", "supplier")])
def test_remote_requests_resolve_registry_each_time_and_keep_signed_contract(monkeypatch, audience, role):
    from app.agents import remote
    key = "r" * 43 if audience == "retail" else "s" * 43
    container = ApplicationContainer(Settings(_env_file=None, agent_endpoints={audience: FALLBACK},
                                               agent_service_keys={audience: key}))
    database = MemoryRegistryDatabase()
    container.database = database
    session = SessionContext(session_id=uuid4(), role=role, supplier_id="SUP001" if role == "supplier" else None,
                             expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    captured = []
    def open_request(request, timeout):
        captured.append(request)
        assert timeout == 250
        # Deliberately return an invalid business response; the existing response
        # contract must still reject it after the outbound signature is checked.
        return nullcontext(SimpleNamespace(status=200, headers={}, read=lambda size: json.dumps(wrap_transport(b'{}')).encode()))
    monkeypatch.setattr(remote, "build_opener", lambda *args: SimpleNamespace(open=open_request))
    client = getattr(container, "remote_" + audience + "_agent")
    service = container.agent_registry_service
    def invoke():
        with pytest.raises(RuntimeError, match="Agent service unavailable"):
            client.invoke("get", {"thread_id": str(uuid4())}, session)
    invoke()
    registered = service.create(AgentRegistration(**registration(agent_type=audience.upper(), active=True)))
    invoke()
    service.set_active(registered.agent_id, False)
    invoke()
    database.fetch_all = Mock(side_effect=db_error(942))
    invoke()
    assert [request.full_url for request in captured] == [
        url + "/internal/agent/invoke" for url in (FALLBACK, REGISTERED, FALLBACK, FALLBACK)]
    for request in captured:
        wire = json.loads(request.data)
        headers = {name.lower(): value for name, value in request.header_items()}
        assert headers["x-agent-signature"] == transport_signature(wire["payload"], key)
        assert "x-agent-probe-proof" in headers
        assert "authorization" not in headers
        envelope = json.loads(decode_transport(wire["payload"], 128000))
        assert envelope["audience"] == audience and envelope["operation"] == "get"
        assert envelope["session"]["session_id"] == str(session.session_id)
        assert envelope["session"]["role"] == role
    assert getattr(container, audience + "_workflow_service").client is client
    if audience == "retail":
        assert container.retail_conversation_service.client is client
    with pytest.raises(PermissionError):
        client.invoke("get", {}, session.model_copy(update={"role": "supplier" if role == "retail-manager" else "retail-manager",
                                                          "supplier_id": "SUP001" if role == "retail-manager" else None}))
    assert len(captured) == 4


def test_no_network_at_construction_and_existing_local_workflows_stay_local():
    container = ApplicationContainer(Settings(_env_file=None))
    database = Mock()
    container.database = database
    assert isinstance(container.agent_registry_service, AgentRegistryService)
    assert container.retail_workflow_service.__class__.__name__ == "RetailWorkflowService"
    assert container.supplier_workflow_service.__class__.__name__ == "SupplierWorkflowService"
    assert container.retail_conversation_service.__class__.__name__ == "RetailConversationService"
    database.fetch_all.assert_not_called()


def test_proxy_allows_only_registry_management_routes():
    assert integrated_target("GET", "registry/agents", "limit=20&offset=0") == "registry/agents?limit=20&offset=0"
    assert integrated_target("POST", "registry/agents", "") == "registry/agents"
    for operation in ("activate", "deactivate"):
        path = f"registry/agents/{uuid4()}/{operation}"
        assert integrated_target("POST", path, "") == path
    for method, path, query in [("DELETE", "registry/agents", ""), ("GET", "registry/agents", "url=bad"),
                                ("POST", "registry/agents/abc/invoke", ""), ("POST", "registry/agents", "active=true")]:
        with pytest.raises(HTTPException):
            integrated_target(method, path, query)


def test_registry_sql_binds_and_transaction_rollback():
    for name, definition in {**READ_OPERATIONS, **WRITE_OPERATIONS}.items():
        if name.startswith("registry_"):
            assert set(re.findall(r":([a-z_]+)\b", definition.sql)) == definition.parameter_names
    database = OracleDatabaseClient(dsn="offline", user="offline", password="offline")
    connection = Mock()
    connection.cursor.return_value.execute.side_effect = db_error(1)
    database._pool = SimpleNamespace(acquire=lambda: connection)
    with pytest.raises(RuntimeError):
        database.execute("registry_set_active", {"agent_id": str(uuid4()), "active": 1})
    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()
    connection.close.assert_called_once()


@pytest.mark.parametrize("table_count,index_count,expected", [(0, 0, "migration_required"), (1, 1, "ready"), (1, 0, None)])
def test_registry_readiness_checks_optional_table_index_and_sql(table_count, index_count, expected):
    database = Mock()
    database.fetch_all.return_value = [{"table_count": table_count, "index_count": index_count}]
    cursor = Mock()
    connection = SimpleNamespace(cursor=lambda: nullcontext(cursor))
    database._get_pool.return_value.acquire.return_value = nullcontext(connection)
    service = ReadinessService(SimpleNamespace(database=database))
    if expected is None:
        with pytest.raises(RuntimeError):
            service._inspect_registry()
    else:
        assert service._inspect_registry() == expected
    if expected == "ready":
        assert cursor.parse.call_count == 6
    else:
        database._get_pool.assert_not_called()


def test_fresh_schema_and_migration_define_same_registry_objects():
    root = Path(__file__).resolve().parents[1]
    schema = (root / "database/schema.sql").read_text()
    migration = (root / "database/migrations/004_agent_registry.sql").read_text()
    for pattern in (r"CREATE TABLE agent_registry \(.*?\n\);", r"CREATE UNIQUE INDEX uq_registry_active_type .*?\n\);"):
        assert re.search(pattern, schema, re.S).group() == re.search(pattern, migration, re.S).group()
