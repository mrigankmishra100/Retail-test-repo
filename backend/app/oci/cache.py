"""OCI Cache boundary for active LangGraph and replenishment workflow state."""

from typing import Any
import json
import re
from threading import RLock, Lock
from contextlib import contextmanager
from time import monotonic
from urllib.parse import urlsplit


class CacheUnavailable(RuntimeError):
    """Cache failures must not silently create a different session store."""


class WorkflowBusy(RuntimeError):
    """A workflow already has a writer; retry after the current request ends."""


class OCICacheClient:
    """Redis-compatible short-term sessions, conversation history and checkpoints.

    Cache data includes the active case, selected item and supplier, pending
    approval, and current graph node. Hosted Conversation State is a separate optional
    future integration. OCI Cache deployment uses a non-sharded primary TLS endpoint.
    """

    def __init__(self, endpoint: str | None = None, *, ttl_seconds: int = 3600,
                 allow_memory: bool = True, clock=monotonic, username=None, password=None,
                 tls_ca_file=None, max_connections=20) -> None:
        self.endpoint = endpoint or None
        if self.endpoint and not self.endpoint.startswith(("redis://", "rediss://")):
            raise ValueError("OCI_CACHE_ENDPOINT must be a redis:// or rediss:// URL")
        if self.endpoint:
            parsed = urlsplit(self.endpoint)
            if not parsed.hostname or parsed.fragment:
                raise ValueError("Invalid cache connection URL")
            if (not allow_memory or parsed.hostname.endswith(".oci.oraclecloud.com")) and parsed.scheme != "rediss":
                raise ValueError("Hosted cache requires TLS using rediss://")
            if parsed.query:
                raise ValueError("Cache URL query options are not allowed; use explicit TLS/auth settings")
        self.username, self.password = username, password
        self.tls_ca_file, self.max_connections = tls_ca_file, max_connections
        if ttl_seconds < 1:
            raise ValueError("Cache TTL must be positive")
        self.ttl_seconds, self.allow_memory = ttl_seconds, allow_memory
        self._clock, self._lock = clock, RLock()
        self._entries: dict[str, tuple[float, str]] = {}
        self._client = None
        self._workflow_locks = {}

    @contextmanager
    def workflow_lock(self, thread_id):
        """Single writer. Redis locks do not expire: crashed writers require operator recovery."""
        key = self._key("workflow_lock:" + thread_id)
        if self.endpoint:
            try:
                lock = self._redis().lock(key, timeout=None, blocking=False, thread_local=False)
                acquired = lock.acquire(blocking=False)
            except Exception as exc:
                raise CacheUnavailable("Workflow lock unavailable") from exc
            if not acquired:
                raise WorkflowBusy("Workflow already running")
            try:
                yield
            finally:
                try:
                    lock.release()
                except Exception as exc:
                    raise CacheUnavailable("Workflow lock release failed") from exc
        else:
            with self._lock:
                self._memory_ready()
                lock = self._workflow_locks.setdefault(key, Lock())
                if not lock.acquire(blocking=False):
                    raise WorkflowBusy("Workflow already running")
            try:
                yield
            finally:
                with self._lock:
                    lock.release()
                    self._workflow_locks.pop(key, None)

    @staticmethod
    def _key(key: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9:_-]{1,200}", key):
            raise ValueError("Invalid cache key")
        return "retail-inventory:v1:" + key

    def _redis(self):
        with self._lock:
            if self._client is None:
                from redis import Redis
                options = {"max_connections": self.max_connections}
                if self.username is not None:
                    options["username"] = self.username
                if self.password is not None:
                    options["password"] = self.password
                if self.endpoint.startswith("rediss://"):
                    options.update(ssl_cert_reqs="required", ssl_check_hostname=True)
                    if self.tls_ca_file:
                        options["ssl_ca_certs"] = self.tls_ca_file
                self._client = Redis.from_url(self.endpoint, decode_responses=True,
                                              socket_connect_timeout=5, socket_timeout=5, **options)
            return self._client

    def _memory_ready(self) -> None:
        if not self.allow_memory:
            raise CacheUnavailable("A shared cache is required outside development")
        now = self._clock()
        for key in [k for k, (expires, _) in self._entries.items() if expires <= now]:
            del self._entries[key]

    def get_state(self, session_id: str) -> dict[str, Any] | None:
        """Load a JSON copy, respecting expiration on either backend."""
        key = self._key(session_id)
        if self.endpoint:
            try:
                value = self._redis().get(key)
            except Exception as exc:
                raise CacheUnavailable("Shared cache read failed") from exc
        else:
            with self._lock:
                self._memory_ready()
                entry = self._entries.get(key)
                value = entry[1] if entry else None
        try:
            result = json.loads(value) if value is not None else None
            if result is not None and not isinstance(result, dict):
                raise ValueError("Expected object")
            return result
        except (ValueError, TypeError) as exc:
            raise CacheUnavailable("Invalid cached state") from exc

    def set_state(self, session_id: str, state: dict[str, Any], *,
                  ttl_seconds: int | None = None, only_if_absent: bool = False) -> bool:
        """Set JSON and TTL atomically; optionally reserve a new key only."""
        key = self._key(session_id)
        ttl = self.ttl_seconds if ttl_seconds is None else ttl_seconds
        if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl < 1:
            raise ValueError("Cache TTL must be a positive integer")
        if not isinstance(state, dict):
            raise ValueError("State must be a JSON object")
        value = json.dumps(state, allow_nan=False)
        if self.endpoint:
            try:
                return bool(self._redis().set(key, value, ex=ttl, nx=only_if_absent))
            except Exception as exc:
                raise CacheUnavailable("Shared cache write failed") from exc
        with self._lock:
            self._memory_ready()
            if only_if_absent and key in self._entries:
                return False
            self._entries[key] = (self._clock() + ttl, value)
        return True

    def delete_state(self, session_id: str) -> bool:
        """Delete state without touching other sessions or workflows."""
        key = self._key(session_id)
        if self.endpoint:
            try:
                return bool(self._redis().delete(key))
            except Exception as exc:
                raise CacheUnavailable("Shared cache delete failed") from exc
        with self._lock:
            self._memory_ready()
            return self._entries.pop(key, None) is not None

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None
            self._entries.clear()

    def ping(self) -> None:
        """Readiness only: no user/checkpoint data is read or written."""
        if self.endpoint:
            try:
                if not self._redis().ping():
                    raise CacheUnavailable("Shared cache unavailable")
            except Exception as exc:
                raise CacheUnavailable("Shared cache unavailable") from exc
        else:
            with self._lock:
                self._memory_ready()
