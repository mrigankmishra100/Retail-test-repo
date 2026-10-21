"""Autonomous Database connections and explicit, restartable schema setup."""
import re
from pathlib import Path

from app.distributor_rag.core.config import ROOT
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool


class OracleDatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore", hide_input_in_errors=True)
    oci_db_user: str = Field(min_length=1)
    oci_db_password: SecretStr
    oci_db_dsn: str = Field(min_length=1)
    oci_db_wallet_dir: str = ""
    oci_db_wallet_password: SecretStr = SecretStr("")

    @model_validator(mode="after")
    def secure_connection(self):
        dsn = self.oci_db_dsn.strip()
        if not self.oci_db_password.get_secret_value():
            raise ValueError("Provide the Autonomous Database application user password")
        alias = bool(re.fullmatch(r"[A-Za-z0-9_.-]+", dsn))
        if alias:
            if not self.oci_db_wallet_dir:
                raise ValueError("A TNS alias requires a wallet directory")
        elif not (dsn.lower().startswith("tcps://") or
                  (dsn.startswith("(") and re.search(r"protocol\s*=\s*tcps\s*\)", dsn, re.IGNORECASE))):
            raise ValueError("Use the Autonomous Database TCPS connection string or a wallet TNS alias")
        if re.search(r"protocol\s*=\s*tcp\s*\)|ssl_server_dn_match\s*=\s*(no|false|off)", dsn, re.IGNORECASE):
            raise ValueError("TLS and server identity verification are required")
        self.oci_db_dsn = dsn
        return self


def create_oracle_engine(settings=None):
    settings = settings or OracleDatabaseSettings()
    args = {"user": settings.oci_db_user, "password": settings.oci_db_password.get_secret_value(),
                "dsn": settings.oci_db_dsn, "ssl_server_dn_match": True, "tcp_connect_timeout": 15}
    if settings.oci_db_wallet_dir:
        directory = Path(settings.oci_db_wallet_dir).expanduser()
        if not directory.is_absolute():
            directory = ROOT / directory
        args.update(config_dir=str(directory), wallet_location=str(directory))
        if settings.oci_db_wallet_password.get_secret_value():
            args["wallet_password"] = settings.oci_db_wallet_password.get_secret_value()
    return create_engine("oracle+oracledb://", connect_args=args, poolclass=NullPool,
                         pool_pre_ping=True, hide_parameters=True)


def create_oracle_repository():
    from app.distributor_rag.providers.oci.repository import OracleRepository
    engine = create_oracle_engine()
    return engine, OracleRepository(sessionmaker(engine, expire_on_commit=False))


VECTOR_TABLES = {
    "distributor_index": """CREATE TABLE distributor_index (
        index_id VARCHAR2(64) NOT NULL, document_id VARCHAR2(36) NOT NULL,
        owner_id VARCHAR2(128) NOT NULL, digest VARCHAR2(64) NOT NULL,
        PRIMARY KEY (index_id, document_id, owner_id))""",
    "distributor_chunks": """CREATE TABLE distributor_chunks (
        index_id VARCHAR2(64) NOT NULL, document_id VARCHAR2(36) NOT NULL,
        owner_id VARCHAR2(128) NOT NULL, chunk_id VARCHAR2(24) NOT NULL,
        content CLOB NOT NULL, page NUMBER(10), embedding VECTOR(*, FLOAT32) NOT NULL,
        PRIMARY KEY (index_id, document_id, owner_id, chunk_id))""",
}


def initialize_schema(engine, *, vector_storage='native'):
    from app.distributor_rag.providers.oci.database_models import Base
    if vector_storage not in {'native', 'json'}:
        raise ValueError('Unsupported Distributor vector storage')
    # Fail before DDL if the selected database cannot execute native vector search.
    if vector_storage == 'native':
        with engine.connect() as connection:
            connection.execute(text("SELECT VECTOR_DISTANCE(TO_VECTOR('[1,0]'), TO_VECTOR('[1,0]'), COSINE) FROM dual")).scalar_one()
    # DDL commits implicitly on Oracle. Each step is safe to repeat after a partial failure.
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("""MERGE INTO distributor_ingestion_locks t USING (SELECT 1 id FROM dual) s
            ON (t.id=s.id) WHEN NOT MATCHED THEN INSERT (id) VALUES (s.id)"""))
    with engine.connect() as connection:
        for name, ddl in VECTOR_TABLES.items():
            if name == 'distributor_chunks' and vector_storage == 'json':
                ddl = ddl.replace('embedding VECTOR(*, FLOAT32) NOT NULL',
                                  'embedding CLOB NOT NULL CHECK (embedding IS JSON)')
            exists = connection.execute(text("SELECT COUNT(*) FROM user_tables WHERE table_name=:name"),
                                        {"name": name.upper()}).scalar_one()
            if not exists:
                connection.execute(text(ddl))
        connection.commit()
