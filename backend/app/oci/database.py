"""Lazy, allowlisted Oracle Database adapter for business data."""

from dataclasses import dataclass
from contextlib import contextmanager
import re
import json
import logging
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Any, Literal

from app.observability.context import TraceContext, request_trace_id
from app.oci.diagnostics import database_stage, log_database_failure

logger = logging.getLogger(__name__)

JSON_TEXT_COLUMNS = frozenset({
    "draft_payload", "payload_snapshot", "result_snapshot", "message_payload", "summary",
    "evidence_metadata",
})


def materialize_row(names, row):
    """Keep stored JSON's text contract across Oracle LOB and decoded JSON results.

    Some Oracle/driver combinations return JSON-constrained LOBs as dicts or lists.
    Case services parse these columns before comparing approval hashes. Serialize
    only decoded containers; preserve existing text and fail on unsupported values.
    """
    result = {}
    for name, value in zip(names, row, strict=True):
        if hasattr(value, "read"):
            value = value.read()
        if name in JSON_TEXT_COLUMNS and isinstance(value, (dict, list)):
            value = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        result[name] = value
    return result


class OracleDatabaseConfigurationError(RuntimeError):
    """Raised when a database operation is attempted without credentials."""


@dataclass(frozen=True)
class DatabaseOperation:
    """A fixed SQL statement and the bind parameters it accepts."""

    sql: str
    parameter_names: frozenset[str]
    kind: Literal["read", "write"]


READ_OPERATIONS: dict[str, DatabaseOperation] = {
    "registry_readiness": DatabaseOperation(
        sql="""SELECT
            (SELECT COUNT(*) FROM user_tables WHERE table_name='AGENT_REGISTRY') AS table_count,
            (SELECT COUNT(*) FROM user_indexes WHERE index_name='UQ_REGISTRY_ACTIVE_TYPE'
                AND table_name='AGENT_REGISTRY' AND status='VALID' AND uniqueness='UNIQUE') AS index_count
            FROM dual""",
        parameter_names=frozenset(), kind="read"),
    "registry_agents": DatabaseOperation(
        sql="""SELECT agent_id,name,agent_type,description,endpoint_url,active,created_at,updated_at
            FROM agent_registry ORDER BY created_at DESC,agent_id DESC
            OFFSET :offset ROWS FETCH NEXT :page_size ROWS ONLY""",
        parameter_names=frozenset({"offset", "page_size"}), kind="read"),
    "registry_agent": DatabaseOperation(
        sql="""SELECT agent_id,name,agent_type,description,endpoint_url,active,created_at,updated_at
            FROM agent_registry WHERE agent_id=:agent_id""",
        parameter_names=frozenset({"agent_id"}), kind="read"),
    "registry_active_endpoint": DatabaseOperation(
        sql="""SELECT endpoint_url FROM agent_registry WHERE agent_type=:agent_type AND active=1
            FETCH FIRST 2 ROWS ONLY""",
        parameter_names=frozenset({"agent_type"}), kind="read"),
    "connection_probe": DatabaseOperation(
        sql="SELECT 1 AS connection_ok FROM dual", parameter_names=frozenset(), kind="read"),
    "nl2sql_inventory": DatabaseOperation(
        sql="""SELECT item.item_id,item.item_name,item.category,item.reorder_point,
                    item.safety_stock,inventory.current_quantity,inventory.last_updated
                FROM items item JOIN inventory ON inventory.item_id=item.item_id
                WHERE (:item_id IS NULL OR item.item_id=:item_id)
                ORDER BY
                    CASE WHEN :sort_by='stock_low_to_high' THEN inventory.current_quantity END ASC NULLS LAST,
                    CASE WHEN :sort_by='stock_high_to_low' THEN inventory.current_quantity END DESC NULLS LAST,
                    item.item_id
                OFFSET 0 ROWS FETCH NEXT :page_size ROWS ONLY""",
        parameter_names=frozenset({"item_id", "sort_by", "page_size"}), kind="read"),
    "nl2sql_sales_summary": DatabaseOperation(
        sql="""SELECT item.item_id,item.item_name,
                    COUNT(sale.sale_id) AS observed_records,
                    NVL(SUM(sale.quantity_sold),0) AS total_quantity_sold,
                    MIN(sale.sale_date) AS first_sale_date,MAX(sale.sale_date) AS last_sale_date
                FROM items item
                LEFT JOIN sales_history sale ON sale.item_id=item.item_id
                    AND sale.sale_date>=TRUNC(SYSDATE)-(:days-1) AND sale.sale_date<=SYSDATE
                WHERE (:item_id IS NULL OR item.item_id=:item_id)
                GROUP BY item.item_id,item.item_name
                ORDER BY
                    CASE WHEN :sort_by='sales_high_to_low' THEN NVL(SUM(sale.quantity_sold),0) END DESC NULLS LAST,
                    CASE WHEN :sort_by='sales_low_to_high' THEN NVL(SUM(sale.quantity_sold),0) END ASC NULLS LAST,
                    item.item_id
                OFFSET 0 ROWS FETCH NEXT :page_size ROWS ONLY""",
        parameter_names=frozenset({"item_id", "days", "sort_by", "page_size"}), kind="read"),
    "nl2sql_supplier_options": DatabaseOperation(
        sql="""SELECT supplier.supplier_id,supplier.supplier_name,supplier_item.item_id,
                    supplier_item.base_price,supplier_item.lead_time_days,
                    supplier_item.minimum_order_quantity,supplier_inventory.available_quantity,
                    supplier_inventory.last_updated AS inventory_last_updated
                FROM supplier_items supplier_item
                JOIN suppliers supplier ON supplier.supplier_id=supplier_item.supplier_id
                LEFT JOIN supplier_inventory
                    ON supplier_inventory.supplier_id=supplier_item.supplier_id
                    AND supplier_inventory.item_id=supplier_item.item_id
                WHERE (:item_id IS NULL OR supplier_item.item_id=:item_id)
                    AND (:supplier_id IS NULL OR supplier.supplier_id=:supplier_id)
                ORDER BY
                    CASE WHEN :sort_by='price_low_to_high' THEN supplier_item.base_price END ASC NULLS LAST,
                    CASE WHEN :sort_by='lead_time_low_to_high' THEN supplier_item.lead_time_days END ASC NULLS LAST,
                    CASE WHEN :sort_by='availability_high_to_low' THEN supplier_inventory.available_quantity END DESC NULLS LAST,
                    supplier_item.item_id,supplier.supplier_id
                OFFSET 0 ROWS FETCH NEXT :page_size ROWS ONLY""",
        parameter_names=frozenset(
            {"item_id", "supplier_id", "sort_by", "page_size"}), kind="read"),
    "approved_memory_page": DatabaseOperation(
        sql="""SELECT m.case_id FROM approved_memory m JOIN replenishment_cases c ON c.case_id=m.case_id
            WHERE c.status='COMPLETED' AND (:item_id IS NULL OR c.item_id=:item_id)
              AND (:supplier_id IS NULL OR c.selected_supplier_id=:supplier_id)
              AND EXISTS (SELECT 1 FROM replenishment_case_events e WHERE e.case_id=c.case_id
                  AND e.event_type='COMPLETED' AND e.case_version=c.version AND e.payload_hash=c.draft_hash)
            ORDER BY m.created_at DESC,m.case_id DESC OFFSET :offset ROWS FETCH NEXT :page_size ROWS ONLY""",
        parameter_names=frozenset({"item_id", "supplier_id", "offset", "page_size"}), kind="read"),
    "supplier_email": DatabaseOperation(
        sql="SELECT email FROM suppliers WHERE supplier_id=:supplier_id",
        parameter_names=frozenset({"supplier_id"}), kind="read"),
    "readiness": DatabaseOperation(
        sql="SELECT COUNT(*) AS table_count FROM user_tables WHERE table_name IN ('ITEMS','INVENTORY','SALES_HISTORY','SUPPLIERS','SUPPLIER_ITEMS','SUPPLIER_INVENTORY','REPLENISHMENT_CASES','REPLENISHMENT_CASE_EVENTS','NOTIFICATION_OUTBOX','APPROVED_MEMORY','CHAT_CONVERSATIONS','CHAT_MESSAGES')",
        parameter_names=frozenset(), kind="read"),
    "chat_conversation": DatabaseOperation(
        sql="""SELECT conversation_id,owner_session_id,owner_role,workflow_thread_id,status,created_at,updated_at
            FROM chat_conversations WHERE conversation_id=:conversation_id""",
        parameter_names=frozenset({"conversation_id"}), kind="read"),
    "chat_messages_page": DatabaseOperation(
        sql="""SELECT message_id,conversation_id,turn_number,role,content,response_route,
                    response_status,guardrail_status,evidence_metadata,error_code,created_at
            FROM (
                SELECT message_id,conversation_id,turn_number,role,content,response_route,
                    response_status,guardrail_status,evidence_metadata,error_code,created_at
                FROM chat_messages WHERE conversation_id=:conversation_id
                ORDER BY turn_number DESC,
                    CASE role WHEN 'ASSISTANT' THEN 1 ELSE 0 END DESC
                FETCH NEXT :page_size ROWS ONLY
            )
            ORDER BY turn_number ASC,
                CASE role WHEN 'USER' THEN 0 ELSE 1 END ASC""",
        parameter_names=frozenset({"conversation_id", "page_size"}), kind="read"),
    "sales_page": DatabaseOperation(
        sql="""SELECT sale_id,item_id,sale_date,quantity_sold FROM sales_history
            WHERE item_id=:item_id AND sale_date>=TRUNC(SYSDATE)-(:days-1) AND sale_date<=SYSDATE
            ORDER BY sale_date,sale_id OFFSET :offset ROWS FETCH NEXT :page_size ROWS ONLY""",
        parameter_names=frozenset({"item_id", "days", "offset", "page_size"}), kind="read"),
    "cases_page": DatabaseOperation(
        sql="""SELECT c.case_id,c.item_id,c.recommended_quantity,c.selected_supplier_id,c.status,c.version,
                n.request_notification_status,n.response_notification_status
            FROM replenishment_cases c
            LEFT JOIN (
                SELECT case_id,
                    MAX(CASE WHEN notification_type='SUPPLIER_REQUEST' THEN status END) AS request_notification_status,
                    MAX(CASE WHEN notification_type='SUPPLIER_RESPONSE' THEN status END) AS response_notification_status
                FROM notification_outbox GROUP BY case_id
            ) n ON n.case_id=c.case_id
            WHERE (:supplier_id IS NULL OR (c.selected_supplier_id=:supplier_id AND c.status NOT IN
                ('DRAFT','AWAITING_MANAGER_APPROVAL','MANAGER_REJECTED','REQUEST_QUEUED')))
            AND EXISTS (SELECT 1 FROM replenishment_case_events e WHERE e.case_id=c.case_id
                        AND e.case_version=0 AND e.event_type='CREATED')
            ORDER BY c.created_at DESC,c.case_id DESC OFFSET :offset ROWS FETCH NEXT :page_size ROWS ONLY""",
        parameter_names=frozenset({"supplier_id", "offset", "page_size"}), kind="read"),
    "supplier_identity": DatabaseOperation(
        sql="SELECT supplier_id FROM suppliers WHERE supplier_id = :supplier_id",
        parameter_names=frozenset({"supplier_id"}), kind="read",
    ),
    "inventory_overview": DatabaseOperation(
        sql="""
            SELECT item.item_id,
                   item.item_name,
                   item.category,
                   item.reorder_point,
                   item.safety_stock,
                   inventory.current_quantity,
                   inventory.last_updated
              FROM items item
              JOIN inventory ON inventory.item_id = item.item_id
             ORDER BY item.item_id
        """,
        parameter_names=frozenset(),
        kind="read",
    ),
    "item_details": DatabaseOperation(
        sql="""
            SELECT item.item_id,
                   item.item_name,
                   item.category,
                   item.reorder_point,
                   item.safety_stock,
                   inventory.current_quantity,
                   inventory.last_updated
              FROM items item
              JOIN inventory ON inventory.item_id = item.item_id
             WHERE item.item_id = :item_id
        """,
        parameter_names=frozenset({"item_id"}),
        kind="read",
    ),
    "item_sales_history": DatabaseOperation(
        sql="""
            SELECT sale_id,
                   item_id,
                   sale_date,
                   quantity_sold,
                   TRUNC(SYSDATE) AS as_of_date
              FROM sales_history
             WHERE item_id = :item_id
               AND sale_date >= TRUNC(SYSDATE) - (:days - 1)
               AND sale_date <= SYSDATE
             ORDER BY sale_date ASC, sale_id ASC
        """,
        parameter_names=frozenset({"item_id", "days"}),
        kind="read",
    ),
    "suppliers_for_item": DatabaseOperation(
        sql="""
            SELECT supplier.supplier_id,
                   supplier.supplier_name,
                   supplier.email,
                   supplier.policy_object_name,
                   supplier_item.item_id,
                   supplier_item.base_price,
                   supplier_item.lead_time_days,
                   supplier_item.minimum_order_quantity,
                   supplier_inventory.available_quantity,
                   supplier_inventory.last_updated AS inventory_last_updated
              FROM supplier_items supplier_item
              JOIN suppliers supplier
                ON supplier.supplier_id = supplier_item.supplier_id
              LEFT JOIN supplier_inventory
                ON supplier_inventory.supplier_id = supplier_item.supplier_id
               AND supplier_inventory.item_id = supplier_item.item_id
             WHERE supplier_item.item_id = :item_id
             ORDER BY supplier.supplier_id
        """,
        parameter_names=frozenset({"item_id"}),
        kind="read",
    ),
    "supplier_inventory": DatabaseOperation(
        sql="""
            SELECT supplier_id,
                   item_id,
                   available_quantity,
                   last_updated
              FROM supplier_inventory
             WHERE supplier_id = :supplier_id
               AND item_id = :item_id
        """,
        parameter_names=frozenset({"supplier_id", "item_id"}),
        kind="read",
    ),
    "supplier_quote_inputs": DatabaseOperation(
        sql="""
            SELECT supplier.supplier_id,
                   supplier.supplier_name,
                   supplier.policy_object_name,
                   supplier_item.item_id,
                   supplier_item.base_price,
                   supplier_item.lead_time_days,
                   supplier_item.minimum_order_quantity,
                   supplier_inventory.available_quantity
              FROM supplier_items supplier_item
              JOIN suppliers supplier
                ON supplier.supplier_id = supplier_item.supplier_id
              LEFT JOIN supplier_inventory
                ON supplier_inventory.supplier_id = supplier_item.supplier_id
               AND supplier_inventory.item_id = supplier_item.item_id
             WHERE supplier_item.supplier_id = :supplier_id
               AND supplier_item.item_id = :item_id
        """,
        parameter_names=frozenset({"supplier_id", "item_id"}),
        kind="read",
    ),
    "get_replenishment_case": DatabaseOperation(
        sql="""
            SELECT case_id,
                   item_id,
                   current_stock,
                   recommended_quantity,
                   selected_supplier_id,
                   status,
                   created_at,
                   updated_at
              FROM replenishment_cases
             WHERE case_id = :case_id
        """,
        parameter_names=frozenset({"case_id"}),
        kind="read",
    ),
    "get_approved_memory": DatabaseOperation(
        sql="""
            SELECT memory_id,
                   case_id,
                   summary,
                   created_at
              FROM approved_memory
             WHERE case_id = :case_id
             ORDER BY created_at ASC, memory_id ASC
        """,
        parameter_names=frozenset({"case_id"}),
        kind="read",
    ),
}


WRITE_OPERATIONS: dict[str, DatabaseOperation] = {
    "registry_create_agent": DatabaseOperation(
        sql="""INSERT INTO agent_registry (agent_id,name,agent_type,description,endpoint_url,active)
            VALUES (:agent_id,:name,:agent_type,:description,:endpoint_url,:active)""",
        parameter_names=frozenset({"agent_id", "name", "agent_type", "description", "endpoint_url", "active"}),
        kind="write"),
    "registry_set_active": DatabaseOperation(
        sql="""UPDATE agent_registry SET active=:active,updated_at=SYSTIMESTAMP WHERE agent_id=:agent_id""",
        parameter_names=frozenset({"agent_id", "active"}), kind="write"),
    "save_chat_turn": DatabaseOperation(
        sql="""DECLARE
                existing_owner VARCHAR2(36);
                existing_role VARCHAR2(20);
                existing_workflow VARCHAR2(36);
                conversation_created BOOLEAN := FALSE;
            BEGIN
                BEGIN
                    SELECT /*+ NO_PARALLEL */ owner_session_id,owner_role,workflow_thread_id
                    INTO existing_owner,existing_role,existing_workflow
                    FROM chat_conversations
                    WHERE conversation_id=:conversation_id;
                EXCEPTION
                    WHEN NO_DATA_FOUND THEN
                        INSERT /*+ NO_PARALLEL */ INTO chat_conversations (
                            conversation_id,owner_session_id,owner_role,workflow_thread_id,status
                        ) VALUES (
                            :conversation_id,:owner_session_id,:owner_role,:workflow_thread_id,'ACTIVE'
                        );
                        existing_owner := :owner_session_id;
                        existing_role := :owner_role;
                        existing_workflow := :workflow_thread_id;
                        conversation_created := TRUE;
                END;

                IF existing_owner<>:owner_session_id OR existing_role<>:owner_role
                   OR NVL(existing_workflow,'-')<>NVL(:workflow_thread_id,'-') THEN
                    RAISE_APPLICATION_ERROR(-20021,'Conversation ownership or workflow mismatch');
                END IF;

                -- Update the parent before inserting child rows. On Autonomous
                -- Database, updating it after FK checks can wait on a sibling
                -- execution server and raise ORA-12860.
                IF NOT conversation_created THEN
                    UPDATE /*+ NO_PARALLEL */ chat_conversations SET updated_at=SYSTIMESTAMP
                    WHERE conversation_id=:conversation_id;
                END IF;

                INSERT /*+ NO_PARALLEL */ INTO chat_messages (
                    message_id,conversation_id,turn_number,role,content,
                    response_status,guardrail_status
                ) VALUES (
                    :user_message_id,:conversation_id,:turn_number,'USER',:user_content,
                    'COMPLETED',:guardrail_status
                );

                INSERT /*+ NO_PARALLEL */ INTO chat_messages (
                    message_id,conversation_id,turn_number,role,content,response_route,
                    response_status,guardrail_status,evidence_metadata,error_code
                ) VALUES (
                    :assistant_message_id,:conversation_id,:turn_number,'ASSISTANT',:assistant_content,
                    :response_route,:response_status,:guardrail_status,:evidence_metadata,:error_code
                );

            END;""",
        parameter_names=frozenset({"conversation_id", "owner_session_id", "owner_role",
            "workflow_thread_id", "user_message_id", "assistant_message_id", "turn_number",
            "user_content", "assistant_content", "response_route", "response_status",
            "guardrail_status", "evidence_metadata", "error_code"}),
        kind="write"),
    "create_replenishment_case": DatabaseOperation(
        sql="""
            INSERT INTO replenishment_cases (
                case_id,
                item_id,
                current_stock,
                recommended_quantity,
                status
            ) VALUES (
                :case_id,
                :item_id,
                :current_stock,
                :recommended_quantity,
                :status
            )
        """,
        parameter_names=frozenset(
            {"case_id", "item_id", "current_stock", "recommended_quantity", "status"}
        ),
        kind="write",
    ),
    "select_case_supplier": DatabaseOperation(
        sql="""
            UPDATE replenishment_cases
               SET selected_supplier_id = :supplier_id,
                   updated_at = SYSTIMESTAMP
             WHERE case_id = :case_id
        """,
        parameter_names=frozenset({"case_id", "supplier_id"}),
        kind="write",
    ),
    "update_case_status": DatabaseOperation(
        sql="""
            UPDATE replenishment_cases
               SET status = :status,
                   updated_at = SYSTIMESTAMP
             WHERE case_id = :case_id
        """,
        parameter_names=frozenset({"case_id", "status"}),
        kind="write",
    ),
    "save_approved_summary": DatabaseOperation(
        sql="""
            INSERT INTO approved_memory (
                memory_id,
                case_id,
                summary
            ) VALUES (
                :memory_id,
                :case_id,
                :summary
            )
        """,
        parameter_names=frozenset({"memory_id", "case_id", "summary"}),
        kind="write",
    ),
}


class OracleDatabaseClient:
    """Execute only named SQL operations through a lazily created pool."""

    def __init__(
        self,
        dsn: str | None = None,
        user: str | None = None,
        password: str | None = None,
        *,
        config_dir: str | None = None,
        driver_mode: Literal['thin', 'thick'] = 'thin',
        wallet_password: str | None = None,
        pool_min: int = 1,
        pool_max: int = 4,
        pool_increment: int = 1,
        retry_count: int = 0,
        retry_delay: int = 1,
        tcp_connect_timeout: float = 10.0,
        runtime_wallet: Any | None = None,
    ) -> None:
        self.dsn = dsn.strip() if dsn else None
        self.user = user.strip() if user else None
        self._password = password
        if driver_mode not in {'thin', 'thick'}:
            raise ValueError('Unsupported database driver mode')
        self.driver_mode = driver_mode
        self.config_dir = str(Path(config_dir).expanduser().resolve()) if config_dir else None
        self._wallet_password = wallet_password
        self.runtime_wallet = runtime_wallet
        self.pool_min = pool_min
        self.pool_max = pool_max
        self.pool_increment = pool_increment
        self.retry_count = retry_count
        self.retry_delay = retry_delay
        self.tcp_connect_timeout = tcp_connect_timeout
        self._pool: Any | None = None
        self._pool_lock = Lock()

    @property
    def is_configured(self) -> bool:
        """Return whether all required connection settings are present."""
        return bool(self.dsn and self.user and self._password)

    @property
    def pool_created(self) -> bool:
        """Expose pool state for readiness diagnostics without opening it."""
        return self._pool is not None

    def fetch_all(
        self,
        operation: str,
        parameters: dict[str, Any] | None = None,
        *,
        trace_context: TraceContext | None = None,
    ) -> list[dict[str, Any]]:
        """Execute an allowlisted read and return stable lowercase-keyed rows."""
        definition, binds = self._resolve_operation(READ_OPERATIONS, operation, parameters)
        started_at = perf_counter()
        connection: Any | None = None
        cursor: Any | None = None
        stage = "pool_initialization"
        try:
            pool = self._get_pool()
            stage = "connection_acquire"
            connection = pool.acquire()
            stage = "cursor_create"
            cursor = connection.cursor()
            stage = "query_execute"
            cursor.execute(definition.sql, binds)
            stage = "result_fetch"
            column_names = [column[0].lower() for column in cursor.description or ()]
            fetched = cursor.fetchall()
            stage = "row_materialization"
            rows = [materialize_row(column_names, row) for row in fetched]
        except Exception as exc:
            log_database_failure(exc, stage=stage, operation=operation, started_at=started_at,
                                 trace_id=trace_context.trace_id if trace_context else None)
            self._log_operation(operation, started_at, "failed", trace_context)
            raise
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()

        self._log_operation(operation, started_at, "success", trace_context)
        return rows

    def execute(
        self,
        operation: str,
        parameters: dict[str, Any] | None = None,
        *,
        trace_context: TraceContext | None = None,
    ) -> bool:
        """Execute one allowlisted write and commit or roll back atomically."""
        definition, binds = self._resolve_operation(WRITE_OPERATIONS, operation, parameters)
        started_at = perf_counter()
        connection: Any | None = None
        cursor: Any | None = None
        stage = "pool_initialization"
        try:
            pool = self._get_pool()
            stage = "connection_acquire"
            connection = pool.acquire()
            stage = "cursor_create"
            cursor = connection.cursor()
            stage = "query_execute"
            cursor.execute(definition.sql, binds)
            changed = cursor.rowcount > 0
            stage = "commit"
            connection.commit()
        except Exception as exc:
            log_database_failure(exc, stage=stage, operation=operation, started_at=started_at,
                                 trace_id=trace_context.trace_id if trace_context else None)
            if connection is not None:
                connection.rollback()
            self._log_operation(operation, started_at, "failed", trace_context)
            raise
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()

        self._log_operation(operation, started_at, "success", trace_context)
        return changed

    def close(self) -> None:
        """Close the pool if it was created and make future use recreate it."""
        with self._pool_lock:
            pool = self._pool
            if pool is not None:
                pool.close()
                self._pool = None
                logger.info("Oracle Database connection pool closed", extra={"status": "closed"})
            if self.runtime_wallet is not None:
                self.runtime_wallet.close()

    @contextmanager
    def case_transaction(self):
        """Commit all case/event/outbox/memory statements once, or roll back all."""
        with database_stage("connection_acquire", "case_transaction"):
            connection = self._get_pool().acquire()
        started = perf_counter()
        stage = "transaction_body"
        try:
            connection.autocommit = False
            yield CaseTransaction(connection)
            stage = "commit"
            connection.commit()
        except BaseException as exc:
            log_database_failure(exc, stage=stage, operation="case_transaction", started_at=started)
            connection.rollback()
            self._log_operation("case_transaction", started, "failed", None)
            raise
        else:
            self._log_operation("case_transaction", started, "success", None)
        finally:
            connection.close()

    def _get_pool(self) -> Any:
        if not self.is_configured:
            raise OracleDatabaseConfigurationError(
                "Oracle Database requires ORACLE_DB_DSN, ORACLE_DB_USER, and ORACLE_DB_PASSWORD"
            )
        if self._pool is None:
            with self._pool_lock:
                if self._pool is None:
                    import oracledb

                    with database_stage("wallet_prepare"):
                        wallet_directory = self.runtime_wallet.prepare() if self.runtime_wallet else self.config_dir
                    if self.runtime_wallet:
                        logger.info("Runtime database wallet prepared", extra={"db_stage": "wallet_prepare", "status": "ready"})

                    if self.driver_mode == 'thick':
                        from app.oci.client_mode import initialize_thick_client
                        with database_stage('client_initialize'):
                            initialize_thick_client(oracledb, wallet_directory)
                        logger.info('Oracle Database driver mode=thick client=%s',
                                    '.'.join(map(str, oracledb.clientversion())),
                                    extra={'db_stage': 'client_initialize', 'status': 'ready'})

                    logger.info(
                        "Creating Oracle Database connection pool",
                        extra={"status": "starting"},
                    )
                    pool_arguments: dict[str, Any] = {
                        "user": self.user,
                        "password": self._password,
                        "min": self.pool_min,
                        "max": self.pool_max,
                        "increment": self.pool_increment,
                    }
                    if wallet_directory:
                        pool_arguments["config_dir"] = wallet_directory
                        pool_arguments["wallet_location"] = wallet_directory
                    if self._wallet_password and self.driver_mode == 'thin':
                        pool_arguments["wallet_password"] = self._wallet_password
                    if self.driver_mode == 'thick':
                        # Parse TNS with Python in both modes; preserve bounded
                        # retries and encode the runtime wallet in the descriptor.
                        pool_arguments['thick_mode_dsn_passthrough'] = False
                    try:
                        with database_stage("pool_parameters"):
                            pool_parameters = oracledb.PoolParams(**pool_arguments)
                        with database_stage("dsn_parse"):
                            pool_parameters.parse_connect_string(self.dsn)
                        pool_parameters.set(
                            retry_count=self.retry_count,
                            retry_delay=self.retry_delay,
                            tcp_connect_timeout=self.tcp_connect_timeout,
                        )
                        if self.driver_mode == 'thick':
                            pool_parameters.set(wallet_location=wallet_directory, ssl_server_dn_match=True)
                        with database_stage("pool_create"):
                            self._pool = oracledb.create_pool(params=pool_parameters)
                        logger.info(
                            "Oracle Database connection pool created",
                            extra={"status": "ready"},
                        )
                    except Exception as exc:
                        if "DPY-4027" in str(exc):
                            raise OracleDatabaseConfigurationError(
                                "ORACLE_DB_CONFIG_DIR must point to the Oracle wallet/TNS directory "
                                "when ORACLE_DB_DSN is a TNS alias"
                            ) from exc
                        raise
        return self._pool

    @staticmethod
    def _resolve_operation(
        operations: dict[str, DatabaseOperation],
        operation: str,
        parameters: dict[str, Any] | None,
    ) -> tuple[DatabaseOperation, dict[str, Any]]:
        try:
            definition = operations[operation]
        except KeyError as exc:
            raise ValueError(f"Oracle Database operation is not allowed: {operation}") from exc

        binds = dict(parameters or {})
        supplied_names = frozenset(binds)
        if supplied_names != definition.parameter_names:
            missing = sorted(definition.parameter_names - supplied_names)
            unexpected = sorted(supplied_names - definition.parameter_names)
            details: list[str] = []
            if missing:
                details.append(f"missing: {', '.join(missing)}")
            if unexpected:
                details.append(f"unexpected: {', '.join(unexpected)}")
            raise ValueError(f"Invalid parameters for {operation} ({'; '.join(details)})")
        return definition, binds

    @staticmethod
    def _log_operation(
        operation: str,
        started_at: float,
        status: Literal["success", "failed"],
        trace_context: TraceContext | None,
    ) -> None:
        metadata = (trace_context or TraceContext(trace_id=request_trace_id.get())).as_metadata(
            db_operation=operation,
            duration_ms=round((perf_counter() - started_at) * 1000, 3),
            status=status,
        )
        logger.info("Oracle Database operation %s: %s", operation, status, extra=metadata)


class CaseTransaction:
    """Private fixed-operation unit of work; consumes LOBs before releasing connection."""

    def __init__(self, connection):
        self.connection = connection

    def run(self, operation: str, **binds):
        from app.oci.case_sql import SQL, CLOB_BINDS
        try:
            sql = SQL[operation]
        except KeyError as exc:
            raise ValueError("Unknown case operation") from exc
        if set(re.findall(r":([a-z_]+)\b", sql)) != set(binds):
            raise ValueError("Invalid case operation parameters")
        cursor = self.connection.cursor()
        try:
            if CLOB_BINDS & binds.keys():
                import oracledb
                cursor.setinputsizes(**{key: oracledb.DB_TYPE_CLOB for key in CLOB_BINDS & binds.keys()})
            cursor.execute(sql, binds)
            if operation in {"case", "origin", "event", "approvals", "decisions", "outbox", "memory", "completion"}:
                names = [column[0].lower() for column in cursor.description]
                return [materialize_row(names, row) for row in cursor.fetchall()]
            return cursor.rowcount
        finally:
            cursor.close()
