-- Optional owner-run setup when automatic CREATE TABLE is not permitted.
-- Private integration credentials only; never expose this table through chat tools.
CREATE TABLE delivery_connections (
    connection_key VARCHAR2(200) PRIMARY KEY,
    agent_id VARCHAR2(36) NOT NULL,
    endpoint_url VARCHAR2(2000) NOT NULL,
    api_token CLOB,
    auth_mode VARCHAR2(6) DEFAULT 'bearer' NOT NULL CHECK (auth_mode IN ('bearer','none')),
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL
);
