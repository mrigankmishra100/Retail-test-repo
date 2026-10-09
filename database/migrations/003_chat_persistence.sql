-- Add durable, session-owned retail chat history.
-- Run once as the existing table owner. The application user must be able to
-- SELECT/INSERT/UPDATE these tables. Oracle DDL commits implicitly.
WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK
SET DEFINE OFF
SET SERVEROUTPUT ON

DECLARE
    n PLS_INTEGER;
BEGIN
    SELECT COUNT(*) INTO n FROM user_tables
    WHERE table_name IN ('CHAT_CONVERSATIONS', 'CHAT_MESSAGES');
    IF n <> 0 THEN
        RAISE_APPLICATION_ERROR(-20031,
            'Chat persistence objects already exist or are partially applied; inspect instead of rerunning.');
    END IF;
END;
/

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

CREATE INDEX ix_chat_conversation_owner
    ON chat_conversations(owner_session_id, status, updated_at);
CREATE INDEX ix_chat_message_conversation
    ON chat_messages(conversation_id, turn_number, created_at);

COMMIT;

SELECT table_name FROM user_tables
WHERE table_name IN ('CHAT_CONVERSATIONS', 'CHAT_MESSAGES')
ORDER BY table_name;
