-- Existing databases only; fresh deployments already include this in schema.sql.
-- Run once as the table owner during a maintenance window. Oracle DDL commits.
WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK
SET DEFINE OFF
CREATE UNIQUE INDEX uq_registry_active_delivery ON agent_registry (
    CASE WHEN active=1 AND agent_type='DELIVERY' THEN agent_type END
);
ALTER TABLE agent_registry ADD CONSTRAINT ck_registry_delivery_roles
    CHECK (agent_type IN ('RETAIL', 'SUPPLIER', 'CUSTOM', 'DELIVERY'));
ALTER TABLE agent_registry DROP CONSTRAINT ck_registry_type;
COMMIT;
