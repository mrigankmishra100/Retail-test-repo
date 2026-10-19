"""Private, expiring distributor credentials shared by hosted gateway and MCP.

Use configured Redis when present, development memory locally, and the existing
Oracle application database for resource-principal deployments without Redis.
Only this integration's credentials use this store; workflow caching is unchanged.
"""
from contextlib import nullcontext


def uses_oracle_connections(settings):
    return (getattr(settings, 'oci_auth_mode', None) == 'resource_principal'
            and not getattr(settings, 'oci_cache_endpoint', None))


class DeliveryConnectionStore:
    def __init__(self, container):
        self.cache = container.cache
        self.database = container.database if uses_oracle_connections(getattr(container, 'settings', None)) else None

    def _ready(self):
        self.database.ensure_delivery_connections()
        self.database.execute('delivery_connection_expire', {})

    def workflow_lock(self, key):
        # Oracle MERGE and conditional DELETE are single atomic statements. Memory
        # and Redis retain the existing lock around their read/modify/write sequence.
        return nullcontext() if self.database is not None else self.cache.workflow_lock(key)

    def get_state(self, key):
        if self.database is None:
            return self.cache.get_state(key)
        self._ready()
        rows = self.database.fetch_all('delivery_connection_get', {'connection_key': key})
        return rows[0] if rows else None

    def set_state(self, key, state, *, ttl_seconds):
        if self.database is None:
            return self.cache.set_state(key, state, ttl_seconds=ttl_seconds)
        if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool) or ttl_seconds < 1:
            raise ValueError('Invalid connection expiry')
        self._ready()
        params = {'connection_key': key, 'agent_id': state['agent_id'],
            'endpoint_url': state['endpoint_url'], 'token': state['token'],
            'auth_mode': state.get('auth_mode', 'bearer'), 'ttl_seconds': ttl_seconds}
        try:
            return self.database.execute('delivery_connection_save', params)
        except Exception as exc:
            # Concurrent first inserts may race at the unique key. Retry MERGE once
            # after Oracle has resolved that competing transaction.
            if not exc.args or getattr(exc.args[0], 'code', None) != 1:
                raise
            return self.database.execute('delivery_connection_save', params)

    def delete_state(self, key, *, agent_id):
        if self.database is None:
            current = self.cache.get_state(key)
            return self.cache.delete_state(key) if current and current.get('agent_id') == str(agent_id) else False
        self._ready()
        return self.database.execute('delivery_connection_delete',
            {'connection_key': key, 'agent_id': str(agent_id)})
