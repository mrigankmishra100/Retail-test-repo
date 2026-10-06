-- Oracle-compatible schema for the Retail Inventory Agent POC.
-- FRESH, EMPTY SCHEMA ONLY. For an existing database, run
-- migrations/001_phase5_case_persistence.sql instead. See database/README.md.
-- SQLcl / SQL*Plus / SQL Developer Run Script (F5).
WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK
SET DEFINE OFF

CREATE TABLE items (
    item_id VARCHAR2(20) PRIMARY KEY,
    item_name VARCHAR2(100) NOT NULL,
    category VARCHAR2(50) NOT NULL,
    reorder_point NUMBER(10) NOT NULL,
    safety_stock NUMBER(10) NOT NULL
);

CREATE TABLE inventory (
    item_id VARCHAR2(20) PRIMARY KEY,
    current_quantity NUMBER(10) NOT NULL,
    last_updated TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT fk_inventory_item FOREIGN KEY (item_id) REFERENCES items(item_id)
);

CREATE TABLE sales_history (
    sale_id NUMBER(12) PRIMARY KEY,
    item_id VARCHAR2(20) NOT NULL,
    sale_date DATE NOT NULL,
    quantity_sold NUMBER(10) NOT NULL,
    CONSTRAINT fk_sales_item FOREIGN KEY (item_id) REFERENCES items(item_id)
);

CREATE TABLE suppliers (
    supplier_id VARCHAR2(20) PRIMARY KEY,
    supplier_name VARCHAR2(100) NOT NULL,
    email VARCHAR2(254) NOT NULL,
    policy_object_name VARCHAR2(255) NOT NULL
);

CREATE TABLE supplier_items (
    supplier_id VARCHAR2(20) NOT NULL,
    item_id VARCHAR2(20) NOT NULL,
    base_price NUMBER(10, 2) NOT NULL,
    lead_time_days NUMBER(5) NOT NULL,
    minimum_order_quantity NUMBER(10) NOT NULL,
    CONSTRAINT pk_supplier_items PRIMARY KEY (supplier_id, item_id),
    CONSTRAINT fk_supplier_items_supplier FOREIGN KEY (supplier_id) REFERENCES suppliers(supplier_id),
    CONSTRAINT fk_supplier_items_item FOREIGN KEY (item_id) REFERENCES items(item_id)
);

CREATE TABLE supplier_inventory (
    supplier_id VARCHAR2(20) NOT NULL,
    item_id VARCHAR2(20) NOT NULL,
    available_quantity NUMBER(10) NOT NULL,
    last_updated TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT pk_supplier_inventory PRIMARY KEY (supplier_id, item_id),
    CONSTRAINT fk_supplier_inventory_supplier FOREIGN KEY (supplier_id) REFERENCES suppliers(supplier_id),
    CONSTRAINT fk_supplier_inventory_item FOREIGN KEY (item_id) REFERENCES items(item_id)
);

CREATE TABLE replenishment_cases (
    case_id VARCHAR2(36) PRIMARY KEY,
    item_id VARCHAR2(20) NOT NULL,
    current_stock NUMBER(10) NOT NULL,
    recommended_quantity NUMBER(10) NOT NULL,
    selected_supplier_id VARCHAR2(20),
    status VARCHAR2(30) NOT NULL,
    created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
    version NUMBER(10) DEFAULT 0 NOT NULL,
    draft_payload CLOB,
    draft_hash VARCHAR2(64),
    CONSTRAINT fk_case_item FOREIGN KEY (item_id) REFERENCES items(item_id),
    CONSTRAINT fk_case_supplier FOREIGN KEY (selected_supplier_id) REFERENCES suppliers(supplier_id),
    CONSTRAINT ck_case_version CHECK (version >= 0),
    CONSTRAINT ck_case_status CHECK (status IN (
        'DRAFT', 'AWAITING_MANAGER_APPROVAL', 'MANAGER_REJECTED',
        'REQUEST_QUEUED', 'REQUEST_SENT', 'AWAITING_SUPPLIER_APPROVAL',
        'SUPPLIER_REJECTED', 'RESPONSE_QUEUED', 'SUPPLIER_RESPONDED', 'COMPLETED'
    )),
    CONSTRAINT ck_case_draft_json CHECK (draft_payload IS JSON STRICT WITH UNIQUE KEYS),
    CONSTRAINT ck_case_draft_pair CHECK (
        (draft_payload IS NULL AND draft_hash IS NULL) OR
        (draft_payload IS NOT NULL AND draft_hash IS NOT NULL AND LENGTH(draft_hash) = 64)
    )
);

CREATE TABLE approved_memory (
    memory_id VARCHAR2(36) PRIMARY KEY,
    case_id VARCHAR2(36) NOT NULL,
    summary CLOB NOT NULL,
    created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT fk_memory_case FOREIGN KEY (case_id) REFERENCES replenishment_cases(case_id),
    CONSTRAINT uq_memory_case UNIQUE (case_id)
);

-- Immutable transition/approval evidence. Backend must write this in the same
-- transaction as the matching case version/status, outbox intent, and final memory.
CREATE TABLE replenishment_case_events (
    event_id VARCHAR2(36) PRIMARY KEY,
    case_id VARCHAR2(36) NOT NULL,
    case_version NUMBER(10) NOT NULL,
    event_type VARCHAR2(30) NOT NULL,
    from_status VARCHAR2(30),
    to_status VARCHAR2(30) NOT NULL,
    actor_role VARCHAR2(20) NOT NULL,
    actor_session_id VARCHAR2(36),
    actor_supplier_id VARCHAR2(20),
    decision VARCHAR2(7),
    decision_comment VARCHAR2(2000),
    idempotency_key VARCHAR2(128) NOT NULL,
    request_hash VARCHAR2(64) NOT NULL,
    payload_snapshot CLOB NOT NULL,
    payload_hash VARCHAR2(64) NOT NULL,
    result_snapshot CLOB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT fk_event_case FOREIGN KEY (case_id) REFERENCES replenishment_cases(case_id),
    CONSTRAINT fk_event_supplier FOREIGN KEY (actor_supplier_id) REFERENCES suppliers(supplier_id),
    CONSTRAINT uq_event_version UNIQUE (case_id, case_version),
    CONSTRAINT uq_event_idempotency UNIQUE (case_id, idempotency_key),
    CONSTRAINT uq_event_approval UNIQUE (event_id, case_id, event_type, decision),
    CONSTRAINT ck_event_version CHECK (case_version >= 0),
    CONSTRAINT ck_event_type CHECK (event_type IN (
        'CREATED', 'STATE_CHANGED', 'MANAGER_DECISION', 'SUPPLIER_DECISION', 'COMPLETED'
    )),
    CONSTRAINT ck_event_actor CHECK (
        (actor_role = 'system' AND actor_session_id IS NULL AND actor_supplier_id IS NULL) OR
        (actor_role = 'retail-manager' AND actor_session_id IS NOT NULL AND actor_supplier_id IS NULL) OR
        (actor_role = 'supplier' AND actor_session_id IS NOT NULL AND actor_supplier_id IS NOT NULL)
    ),
    CONSTRAINT ck_event_decision CHECK (
        (event_type = 'MANAGER_DECISION' AND actor_role = 'retail-manager'
            AND decision IS NOT NULL AND decision IN ('APPROVE', 'REJECT')) OR
        (event_type = 'SUPPLIER_DECISION' AND actor_role = 'supplier'
            AND decision IS NOT NULL AND decision IN ('APPROVE', 'REJECT')) OR
        (event_type IN ('CREATED', 'STATE_CHANGED', 'COMPLETED') AND decision IS NULL)
    ),
    CONSTRAINT ck_event_edge CHECK (
        (event_type = 'CREATED' AND from_status IS NULL AND to_status = 'DRAFT') OR
        (event_type <> 'CREATED' AND from_status IS NOT NULL AND from_status <> to_status)
    ),
    CONSTRAINT ck_event_hashes CHECK (LENGTH(request_hash) = 64 AND LENGTH(payload_hash) = 64),
    CONSTRAINT ck_event_payload_json CHECK (payload_snapshot IS JSON STRICT WITH UNIQUE KEYS),
    CONSTRAINT ck_event_result_json CHECK (result_snapshot IS JSON STRICT WITH UNIQUE KEYS)
);

-- Only APPROVE decisions of the corresponding kind can have an outbox intent.
-- The composite foreign key also prevents referencing another case's approval.
CREATE TABLE notification_outbox (
    notification_id VARCHAR2(36) PRIMARY KEY,
    case_id VARCHAR2(36) NOT NULL,
    approval_event_id VARCHAR2(36) NOT NULL,
    approval_event_type VARCHAR2(30) NOT NULL,
    approval_decision VARCHAR2(7) DEFAULT 'APPROVE' NOT NULL,
    notification_type VARCHAR2(30) NOT NULL,
    destination VARCHAR2(1000) NOT NULL,
    message_payload CLOB NOT NULL,
    status VARCHAR2(12) DEFAULT 'PENDING' NOT NULL,
    attempt_count NUMBER(10) DEFAULT 0 NOT NULL,
    next_attempt_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    claim_token VARCHAR2(36),
    lease_expires_at TIMESTAMP WITH TIME ZONE,
    provider_message_id VARCHAR2(255),
    last_error_code VARCHAR2(100),
    sent_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT fk_outbox_approval FOREIGN KEY
        (approval_event_id, case_id, approval_event_type, approval_decision)
        REFERENCES replenishment_case_events(event_id, case_id, event_type, decision),
    CONSTRAINT uq_outbox_approval UNIQUE (approval_event_id, notification_type),
    CONSTRAINT ck_outbox_approval CHECK (approval_decision = 'APPROVE'),
    CONSTRAINT ck_outbox_kind CHECK (
        (notification_type = 'SUPPLIER_REQUEST' AND approval_event_type = 'MANAGER_DECISION') OR
        (notification_type = 'SUPPLIER_RESPONSE' AND approval_event_type = 'SUPPLIER_DECISION')
    ),
    CONSTRAINT ck_outbox_status CHECK (status IN ('PENDING', 'SENDING', 'SENT', 'FAILED', 'UNKNOWN')),
    CONSTRAINT ck_outbox_attempts CHECK (attempt_count >= 0),
    CONSTRAINT ck_outbox_payload_json CHECK (message_payload IS JSON STRICT WITH UNIQUE KEYS),
    CONSTRAINT ck_outbox_claim CHECK (
        (status = 'SENDING' AND claim_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR
        (status <> 'SENDING' AND claim_token IS NULL AND lease_expires_at IS NULL)
    ),
    CONSTRAINT ck_outbox_sent CHECK (
        (status = 'SENT' AND sent_at IS NOT NULL) OR
        (status <> 'SENT' AND sent_at IS NULL)
    )
);

CREATE TABLE chat_conversations (
    conversation_id VARCHAR2(36) PRIMARY KEY,
    owner_session_id VARCHAR2(36) NOT NULL,
    owner_role VARCHAR2(20) NOT NULL,
    workflow_thread_id VARCHAR2(36),
    status VARCHAR2(12) DEFAULT 'ACTIVE' NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT ck_chat_conversation_role CHECK (owner_role IN ('retail-manager', 'supplier')),
    CONSTRAINT ck_chat_conversation_status CHECK (status IN ('ACTIVE', 'CLOSED'))
);

CREATE TABLE chat_messages (
    message_id VARCHAR2(36) PRIMARY KEY,
    conversation_id VARCHAR2(36) NOT NULL,
    turn_number NUMBER(10) NOT NULL,
    role VARCHAR2(10) NOT NULL,
    content CLOB NOT NULL,
    response_route VARCHAR2(12),
    response_status VARCHAR2(12),
    guardrail_status VARCHAR2(12),
    evidence_metadata CLOB,
    error_code VARCHAR2(100),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT fk_chat_message_conversation FOREIGN KEY (conversation_id)
        REFERENCES chat_conversations(conversation_id) ON DELETE CASCADE,
    CONSTRAINT uq_chat_message_turn_role UNIQUE (conversation_id, turn_number, role),
    CONSTRAINT ck_chat_message_turn CHECK (turn_number >= 1),
    CONSTRAINT ck_chat_message_role CHECK (role IN ('USER', 'ASSISTANT')),
    CONSTRAINT ck_chat_message_route CHECK (response_route IS NULL OR response_route IN ('LLM', 'NL2SQL', 'BLOCKED')),
    CONSTRAINT ck_chat_message_status CHECK (response_status IS NULL OR response_status IN ('PENDING', 'COMPLETED', 'FAILED', 'BLOCKED')),
    CONSTRAINT ck_chat_message_guardrail CHECK (guardrail_status IS NULL OR guardrail_status IN ('PASSED', 'BLOCKED', 'FAILED', 'NOT_APPLIED')),
    CONSTRAINT ck_chat_message_evidence CHECK (evidence_metadata IS JSON STRICT WITH UNIQUE KEYS)
);

CREATE INDEX ix_case_supplier_status ON replenishment_cases(selected_supplier_id, status);
CREATE INDEX ix_event_supplier ON replenishment_case_events(actor_supplier_id);
CREATE INDEX ix_outbox_dispatch ON notification_outbox(status, next_attempt_at);
CREATE INDEX ix_outbox_case ON notification_outbox(case_id);
CREATE INDEX ix_chat_conversation_owner ON chat_conversations(owner_session_id, status, updated_at);
CREATE INDEX ix_chat_message_conversation ON chat_messages(conversation_id, turn_number, created_at);

CREATE TABLE agent_registry (
    agent_id VARCHAR2(36) PRIMARY KEY,
    name VARCHAR2(120 CHAR) NOT NULL,
    agent_type VARCHAR2(8) NOT NULL,
    description VARCHAR2(1000 CHAR),
    endpoint_url VARCHAR2(2000 CHAR) NOT NULL,
    active NUMBER(1) DEFAULT 0 NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT ck_registry_type CHECK (agent_type IN ('RETAIL', 'SUPPLIER', 'CUSTOM')),
    CONSTRAINT ck_registry_active CHECK (active IN (0, 1))
);

CREATE UNIQUE INDEX uq_registry_active_type ON agent_registry (
    CASE WHEN active=1 AND agent_type IN ('RETAIL', 'SUPPLIER') THEN agent_type END
);
