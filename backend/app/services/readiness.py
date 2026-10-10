"""Bounded, single-flight readiness checks. No work starts at import/startup."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextvars import copy_context
from copy import deepcopy
import logging
from threading import Lock
from time import monotonic, perf_counter

from app.services.policy_terms import parse_policy_terms
from app.oci.diagnostics import log_database_failure


class ReadinessService:
    def __init__(self, container, *, wait_seconds=5, ttl_seconds=15):
        self.container = container
        self.wait_seconds, self.ttl_seconds = wait_seconds, ttl_seconds
        self._lock = Lock()
        self._executor = None
        self._future = None
        self._cached = None
        self._checked_at = 0
        self._closed = False

    def check(self):
        with self._lock:
            if self._closed:
                return {"status": "not_ready", "checks": {"probe": "closed"}}
            if self._cached is not None and monotonic()-self._checked_at < self.ttl_seconds:
                return deepcopy(self._cached)
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="readiness")
            if self._future is None:
                self._future = self._executor.submit(copy_context().run, self._inspect)
            future = self._future
        try:
            result = future.result(timeout=self.wait_seconds)
        except TimeoutError:
            return {"status": "not_ready", "checks": {"probe": "in_progress_or_timed_out"}}
        except Exception:
            result = {"status": "not_ready", "checks": {"probe": "unavailable"}}
        with self._lock:
            if self._future is future:
                self._future = None
                self._cached, self._checked_at = result, monotonic()
        return deepcopy(result)

    def _inspect(self):
        checks = {}
        container = self.container
        started = perf_counter()
        stage = "readiness_query"
        operation = "readiness"
        try:
            if getattr(container.database, 'driver_mode', 'thin') == 'thick':
                stage, operation = 'connection_probe', 'connection_probe'
                if container.database.fetch_all('connection_probe') != [{'connection_ok': 1}]:
                    raise RuntimeError('Database connection probe failed')
                logging.getLogger(__name__).info(
                    'Oracle Database read-only connection probe succeeded (mode=thick)',
                    extra={'db_stage': 'connection_probe', 'status': 'ready'})
                stage, operation = 'readiness_query', 'readiness'
            rows = container.database.fetch_all("readiness")
            stage = "schema_table_validation"
            if rows != [{"table_count": 12}]:
                raise RuntimeError("missing_schema")
            # Query parsing verifies runtime columns/binds without executing any DML.
            from app.oci.case_sql import SQL
            stage = "schema_connection_acquire"
            with container.database._get_pool().acquire() as connection:
                stage = "schema_cursor_create"
                connection.call_timeout = 5000
                with connection.cursor() as cursor:
                    for operation, sql in SQL.items():
                        stage = "schema_statement_validation"
                        if sql.split()[0].upper() not in {"SELECT", "INSERT", "UPDATE"} or ";" in sql:
                            raise ValueError("Unsafe readiness statement")
                        stage = "schema_statement_parse"
                        cursor.parse(sql)
            checks["database"] = "ready"
        except Exception as exc:
            log_database_failure(exc, stage=stage, operation=operation, started_at=started)
            checks["database"] = "unavailable"
        try:
            checks["agent_registry"] = self._inspect_registry()
        except Exception as exc:
            log_database_failure(exc, stage="registry_schema_validation", operation="registry_readiness",
                                 started_at=started)
            checks["agent_registry"] = "unavailable"
        try:
            container.cache.ping()
            checks["sessions"] = "shared_cache_ready" if container.cache.endpoint else "development_memory_only"
        except Exception:
            checks["sessions"] = "unavailable"
        try:
            sources = set()
            for supplier in ("SUP001", "SUP002", "SUP003", "SUP004"):
                policy = container.policy_service.get_supplier_policy(supplier)
                parse_policy_terms(policy)
                sources.add(policy["source"])
            checks["policies"] = "ready" if sources == {"object_storage"} else "local_fallback"
        except Exception:
            checks["policies"] = "unavailable"
        if container.settings.oci_notification_publish_enabled:
            try:
                container.notifications.ensure_ready()
                container.notifications._get_client()  # Topic metadata only, never publishes.
                checks["notifications"] = "configured_topic_reachable"
            except Exception:
                checks["notifications"] = "unavailable"
        else:
            checks["notifications"] = "disabled"
        for name in ("responses", "conversation_state", "nl2sql", "guardrails", "mcp_safety", "agent_registry", "mcp_registry"):
            enabled = getattr(container.settings, "enterprise_ai_" + name + "_enabled")
            # Model assistance is optional: no paid inference in readiness, and no claim of reachability.
            checks["enterprise_ai_" + name] = (
                "configured_not_probed" if enabled and name in {"responses", "conversation_state", "nl2sql"}
                else "enabled_but_unverified" if enabled else "disabled")
        if container.settings.app_env != "development":
            checks["identity"] = "production_identity_not_implemented"
        ready = not any(value in {"unavailable", "enabled_but_unverified", "production_identity_not_implemented"}
                        for value in checks.values())
        return {"status": "ready" if ready else "not_ready", "checks": checks}

    def _inspect_registry(self):
        """Report the optional migration without making old deployments unready."""
        from app.oci.database import READ_OPERATIONS, WRITE_OPERATIONS
        rows = self.container.database.fetch_all("registry_readiness")
        if rows == [{"table_count": 0, "index_count": 0}]:
            return "migration_required"
        if rows != [{"table_count": 1, "index_count": 1}]:
            raise RuntimeError("Incomplete agent registry migration")
        with self.container.database._get_pool().acquire() as connection:
            connection.call_timeout = 5000
            with connection.cursor() as cursor:
                for name, definition in {**READ_OPERATIONS, **WRITE_OPERATIONS}.items():
                    if name.startswith("registry_"):
                        cursor.parse(definition.sql)
        return "ready"

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            future = self._future
            if self._executor is not None:
                # A running SDK call uses its own timeout; never start another probe while it is pending.
                self._executor.shutdown(wait=False, cancel_futures=True)
                self._executor = None
        if future is not None and not future.done():
            future.add_done_callback(lambda _: self.container.close())
        else:
            self.container.close()
