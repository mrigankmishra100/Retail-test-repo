"""Separate gateway/MCP processes must share a private connection without Redis."""
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.config import Settings
from app.oci.cache import OCICacheClient
from app.oci.database import OracleDatabaseClient, READ_OPERATIONS, WRITE_OPERATIONS
from app.services.agent_registry import AgentRegistryService, AgentRegistration
from app.services.delivery_agent import DeliveryAgentService
from app.services.delivery_connections import DeliveryConnectionStore, uses_oracle_connections
from tests.test_agent_registry import MemoryRegistryDatabase, registration, db_error
from tests.test_supplier_conversation import actor
from tests.test_delivery_agent import rest_stub, URL, TOKEN


class ConnectionDatabase(MemoryRegistryDatabase):
    def __init__(self):
        super().__init__()
        self.connections = {}
        self.now = 0

    def ensure_delivery_connections(self):
        pass

    def fetch_all(self, operation, parameters=None):
        if operation == 'delivery_connection_get':
            OracleDatabaseClient._resolve_operation(READ_OPERATIONS, operation, parameters)
            value = self.connections.get(parameters['connection_key'])
            return [{k: value[k] for k in ('agent_id', 'endpoint_url', 'token', 'auth_mode')}] if value and value['expires'] > self.now else []
        return super().fetch_all(operation, parameters)

    def execute(self, operation, parameters):
        if not operation.startswith('delivery_connection_'):
            return super().execute(operation, parameters)
        OracleDatabaseClient._resolve_operation(WRITE_OPERATIONS, operation, parameters)
        if operation == 'delivery_connection_expire':
            for key in list(self.connections):
                if self.connections[key]['expires'] <= self.now:
                    del self.connections[key]
            return True
        key = parameters['connection_key']
        if operation == 'delivery_connection_save':
            self.connections[key] = {**parameters, 'token': parameters['token'] or None,
                'expires': self.now + parameters['ttl_seconds']}
            return True
        if operation == 'delivery_connection_delete':
            if self.connections.get(key, {}).get('agent_id') == parameters['agent_id']:
                del self.connections[key]
                return True
            return False
        raise AssertionError(operation)


@pytest.mark.parametrize('token', [TOKEN, ''])
def test_separate_hosted_processes_share_delivery_connection_and_expiry(rest_stub, token):
    database, who = ConnectionDatabase(), actor()
    def process():
        return SimpleNamespace(settings=Settings(_env_file=None, oci_auth_mode='resource_principal'),
            database=database, cache=OCICacheClient(), agent_registry_service=AgentRegistryService(database))
    gateway, mcp = process(), process()
    agent = gateway.agent_registry_service.create(AgentRegistration(**registration(
        agent_type='CUSTOM', active=True, endpoint_url=URL)))
    first, second = DeliveryAgentService(gateway), DeliveryAgentService(mcp)
    first.connect(agent.agent_id, token, session=who)
    assert gateway.cache.get_state(first._key(who)) is None
    assert mcp.cache.get_state(first._key(who)) is None
    assert second.connection_status(session=who)['available']
    assert not second.connection_status(session=actor())['connected']
    assert second.ask('BlueDart timelines?', session=who).status == 'completed'
    assert len(rest_stub[0]) == 1
    assert rest_stub[0][0][1] == ('Bearer ' + token if token else None)
    another = gateway.agent_registry_service.create(AgentRegistration(**registration(
        agent_type='CUSTOM', active=True, endpoint_url=URL)))
    second.connect(another.agent_id, TOKEN, session=who)
    first.disconnect(agent.agent_id, session=who)
    assert second.connection_status(session=who)['agent_id'] == str(another.agent_id)
    second.disconnect(another.agent_id, session=who)
    assert not first.connection_status(session=who)['connected']
    first.connect(agent.agent_id, TOKEN, session=who)
    database.now += 7200
    assert not second.connection_status(session=who)['connected']
    assert not database.connections


@pytest.mark.parametrize('mode,endpoint,expected', [
    ('config_file', None, False), ('resource_principal', None, True),
    ('resource_principal', 'rediss://cache.example.com:6379', False)])
def test_storage_selection(mode, endpoint, expected):
    assert uses_oracle_connections(SimpleNamespace(oci_auth_mode=mode, oci_cache_endpoint=endpoint)) is expected


def test_hosted_store_failure_does_not_fall_back_to_process_memory():
    database, cache = Mock(), Mock()
    database.ensure_delivery_connections.side_effect = RuntimeError('schema unavailable')
    store = DeliveryConnectionStore(SimpleNamespace(settings=SimpleNamespace(oci_auth_mode='resource_principal'),
        database=database, cache=cache))
    with pytest.raises(RuntimeError):
        store.set_state('key', {'agent_id':str(uuid4()), 'endpoint_url':URL, 'token':TOKEN}, ttl_seconds=60)
    cache.set_state.assert_not_called()


@pytest.mark.parametrize('existing,concurrent', [(True, False), (False, False), (False, True)])
def test_connection_table_creation_is_fixed_and_idempotent(existing, concurrent):
    database, connection, cursor = OracleDatabaseClient(), Mock(), Mock()
    database._get_pool = Mock(return_value=SimpleNamespace(acquire=lambda: nullcontext(connection)))
    connection.cursor.return_value = nullcontext(cursor)
    cursor.execute.side_effect = [None, None] if existing else [db_error(942), db_error(955) if concurrent else None, None, None]
    cursor.fetchone.return_value = ('Y',)
    database.ensure_delivery_connections()
    database.ensure_delivery_connections()
    assert cursor.execute.call_count == (2 if existing else 4)
    assert 'WHERE 1=0' in cursor.execute.call_args_list[0].args[0]
    if not existing:
        assert 'CREATE TABLE delivery_connections' in cursor.execute.call_args_list[1].args[0]
        assert 'WHERE 1=0' in cursor.execute.call_args_list[2].args[0]
    cursor.parse.assert_called_once_with(WRITE_OPERATIONS['delivery_connection_save'].sql)


def test_denied_connection_table_setup_does_not_claim_ready():
    database, connection, cursor = OracleDatabaseClient(), Mock(), Mock()
    database._get_pool = Mock(return_value=SimpleNamespace(acquire=lambda: nullcontext(connection)))
    connection.cursor.return_value = nullcontext(cursor)
    cursor.execute.side_effect = [db_error(942), db_error(1031)]
    with pytest.raises(RuntimeError):
        database.ensure_delivery_connections()
    assert not database._delivery_connections_ready


@pytest.mark.parametrize('concurrent', [False, True])
def test_credential_only_table_upgrade_preserves_rows(concurrent):
    database, connection, cursor = OracleDatabaseClient(), Mock(), Mock()
    database._get_pool = Mock(return_value=SimpleNamespace(acquire=lambda: nullcontext(connection)))
    connection.cursor.return_value = nullcontext(cursor)
    cursor.execute.side_effect = [db_error(904), db_error(1430) if concurrent else None,
        None, None, db_error(1451) if concurrent else None]
    cursor.fetchone.return_value = ('N',)
    database.ensure_delivery_connections()
    statements = [call.args[0] for call in cursor.execute.call_args_list]
    assert "DEFAULT 'bearer'" in statements[1]
    assert statements[-1] == 'ALTER TABLE delivery_connections MODIFY api_token NULL'
    assert not any('DROP' in sql or 'DELETE' in sql for sql in statements)
    assert database._delivery_connections_ready


def test_connection_sql_is_expiring_and_disconnect_is_destination_scoped():
    assert 'expires_at>SYSTIMESTAMP' in READ_OPERATIONS['delivery_connection_get'].sql
    assert 'agent_id=:agent_id' in WRITE_OPERATIONS['delivery_connection_delete'].sql
    assert 'NUMTODSINTERVAL(:ttl_seconds' in WRITE_OPERATIONS['delivery_connection_save'].sql
    assert TOKEN not in repr(READ_OPERATIONS) + repr(WRITE_OPERATIONS)
