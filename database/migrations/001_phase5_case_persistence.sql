-- Phase 5: additive, one-time migration for the EXISTING baseline schema.
-- DO NOT run after the updated database/schema.sql; it already includes this.
-- Stop all application writers and take a recoverable backup before running.
-- Run as the table owner in SQLcl / SQL*Plus / SQL Developer Run Script (F5).
-- Oracle DDL implicitly commits: ROLLBACK CANNOT undo earlier successful DDL.
-- A partial run must be inspected/reconciled; do not drop tables or retry blindly.
WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK
SET DEFINE OFF
SET SERVEROUTPUT ON

PROMPT Phase 5 preflight: no changes have been made yet.
DECLARE
    n PLS_INTEGER;
BEGIN
    SELECT COUNT(*) INTO n FROM user_tables
    WHERE table_name IN ('ITEMS', 'INVENTORY', 'SALES_HISTORY', 'SUPPLIERS',
        'SUPPLIER_ITEMS', 'SUPPLIER_INVENTORY', 'REPLENISHMENT_CASES', 'APPROVED_MEMORY');
    IF n <> 8 THEN
        RAISE_APPLICATION_ERROR(-20001, 'Baseline tables missing in this schema. Verify the connection/table owner.');
    END IF;

    SELECT COUNT(*) INTO n FROM user_objects
    WHERE object_name IN ('REPLENISHMENT_CASE_EVENTS', 'NOTIFICATION_OUTBOX',
        'IX_CASE_SUPPLIER_STATUS', 'IX_EVENT_SUPPLIER', 'IX_OUTBOX_DISPATCH', 'IX_OUTBOX_CASE');
    IF n <> 0 THEN
        RAISE_APPLICATION_ERROR(-20002, 'Phase 5 objects already exist. Migration applied/partial or name collision; inspect before proceeding.');
    END IF;

    SELECT COUNT(*) INTO n FROM user_tab_columns
    WHERE table_name = 'REPLENISHMENT_CASES'
        AND column_name IN ('VERSION', 'DRAFT_PAYLOAD', 'DRAFT_HASH');
    IF n <> 0 THEN
        RAISE_APPLICATION_ERROR(-20003, 'Phase 5 case columns already exist. Inspect an applied/partial migration; do not rerun blindly.');
    END IF;

    SELECT COUNT(*) INTO n FROM user_constraints
    WHERE constraint_name IN ('CK_CASE_VERSION', 'CK_CASE_STATUS', 'CK_CASE_DRAFT_JSON',
        'CK_CASE_DRAFT_PAIR', 'UQ_MEMORY_CASE', 'FK_EVENT_CASE', 'FK_EVENT_SUPPLIER',
        'UQ_EVENT_VERSION', 'UQ_EVENT_IDEMPOTENCY', 'UQ_EVENT_APPROVAL',
        'CK_EVENT_VERSION', 'CK_EVENT_TYPE', 'CK_EVENT_ACTOR', 'CK_EVENT_DECISION',
        'CK_EVENT_EDGE', 'CK_EVENT_HASHES', 'CK_EVENT_PAYLOAD_JSON', 'CK_EVENT_RESULT_JSON',
        'FK_OUTBOX_APPROVAL', 'UQ_OUTBOX_APPROVAL', 'CK_OUTBOX_APPROVAL',
        'CK_OUTBOX_KIND', 'CK_OUTBOX_STATUS', 'CK_OUTBOX_ATTEMPTS',
        'CK_OUTBOX_PAYLOAD_JSON', 'CK_OUTBOX_CLAIM', 'CK_OUTBOX_SENT');
    IF n <> 0 THEN
        RAISE_APPLICATION_ERROR(-20004, 'Phase 5 constraint names already exist. Inspect the schema before proceeding.');
    END IF;

    -- Dynamic SELECT defers table resolution until after the owner/table check.
    EXECUTE IMMEDIATE
        'SELECT COUNT(*) FROM (SELECT case_id FROM approved_memory GROUP BY case_id HAVING COUNT(*) > 1)'
        INTO n;
    IF n <> 0 THEN
        RAISE_APPLICATION_ERROR(-20005, 'Duplicate approved_memory rows per case. Review them manually; this migration will not delete data.');
    END IF;

    EXECUTE IMMEDIATE q'[
        SELECT COUNT(*) FROM replenishment_cases
        WHERE status IS NULL OR status NOT IN (
        'DRAFT', 'AWAITING_MANAGER_APPROVAL', 'MANAGER_REJECTED',
        'REQUEST_QUEUED', 'REQUEST_SENT', 'AWAITING_SUPPLIER_APPROVAL',
        'SUPPLIER_REJECTED', 'RESPONSE_QUEUED', 'SUPPLIER_RESPONDED', 'COMPLETED'
        )
    ]' INTO n;
    IF n <> 0 THEN
        RAISE_APPLICATION_ERROR(-20006, 'Unrecognized existing case status. Review/map legacy cases before adding the status constraint.');
    END IF;
    DBMS_OUTPUT.PUT_LINE('Preflight passed. DDL begins now; earlier successful DDL cannot be rolled back.');
END;
/

ALTER TABLE replenishment_cases ADD (
    version NUMBER(10) DEFAULT 0 NOT NULL,
    draft_payload CLOB,
    draft_hash VARCHAR2(64),
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

ALTER TABLE approved_memory ADD CONSTRAINT uq_memory_case UNIQUE (case_id);

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

CREATE INDEX ix_case_supplier_status ON replenishment_cases(selected_supplier_id, status);
CREATE INDEX ix_event_supplier ON replenishment_case_events(actor_supplier_id);
CREATE INDEX ix_outbox_dispatch ON notification_outbox(status, next_attempt_at);
CREATE INDEX ix_outbox_case ON notification_outbox(case_id);

PROMPT Phase 5 DDL completed. Run database/verify_phase5.sql and retain its output.
PROMPT Schema alone does not verify backend transactional persistence or dispatch.
