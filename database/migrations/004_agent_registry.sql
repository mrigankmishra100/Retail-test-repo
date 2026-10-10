-- Add the optional application Agent Registry. Run once as the existing table
-- owner using SQL*Plus or SQLcl. Oracle DDL commits implicitly.
WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK
SET DEFINE OFF
SET SERVEROUTPUT ON

DECLARE
    n PLS_INTEGER;
BEGIN
    SELECT COUNT(*) INTO n FROM user_objects
    WHERE object_name IN ('AGENT_REGISTRY', 'UQ_REGISTRY_ACTIVE_TYPE');
    IF n <> 0 THEN
        RAISE_APPLICATION_ERROR(-20041,
            'Agent registry objects already exist or are partially applied; inspect instead of rerunning.');
    END IF;
END;
/

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

-- NULL index values allow multiple inactive entries and multiple CUSTOM entries.
-- At most one active destination may serve each existing remote-agent contract.
CREATE UNIQUE INDEX uq_registry_active_type ON agent_registry (
    CASE WHEN active=1 AND agent_type IN ('RETAIL', 'SUPPLIER') THEN agent_type END
);

COMMIT;

SELECT table_name FROM user_tables WHERE table_name='AGENT_REGISTRY';
SELECT index_name,status FROM user_indexes WHERE index_name='UQ_REGISTRY_ACTIVE_TYPE';
