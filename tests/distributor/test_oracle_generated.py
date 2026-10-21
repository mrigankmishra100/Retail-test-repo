from array import array
from types import SimpleNamespace
from unittest.mock import MagicMock
import io
import os
import runpy
from pathlib import Path
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import oracle
from sqlalchemy.orm import sessionmaker
from app.distributor_rag.domain.models import Document, QuestionAnswer, Source, Status
from app.distributor_rag.domain.errors import ProviderError
from app.distributor_rag.providers.oci.database import OracleDatabaseSettings, create_oracle_engine, initialize_schema
from app.distributor_rag.providers.oci.database_models import Base, IngestionLockRow
from app.distributor_rag.providers.oci.repository import OracleRepository
from app.distributor_rag.providers.oci.ingestion import OCIIngestionProvider
from tests.distributor.test_oci_generated import settings


def test_oracle_metadata_ddl_and_json_history_round_trip():
    ddl = "\n".join(str(CreateTable(t).compile(dialect=oracle.dialect())) for t in Base.metadata.sorted_tables)
    assert "CLOB" in ddl and "TIMESTAMP WITH TIME ZONE" in ddl
    assert "ON DELETE RESTRICT" not in ddl
    # SQLite verifies repository contracts and JSON serialization, not Oracle connectivity.
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions.begin() as session:
        session.add(IngestionLockRow(id=1))
    repo = OracleRepository(sessions)
    doc = Document(original_filename="facts.txt", storage_key="documents/facts.txt", content_type="text/plain")
    repo.save_document_metadata(doc)
    assert repo.get_document(doc.id).original_filename == doc.original_filename
    with repo.ingestion_lock():
        repo.update_document_status(doc.id, Status.READY, "", "job")
    assert repo.get_document(doc.id).status_message == ""
    assert repo.list_uploaded_documents()[0].ingestion_status == Status.READY
    answer = QuestionAnswer(document_id=doc.id, question="Name?", answer="Atique Ahmed",
        sources=[Source(source_id="chunk", document_name="facts.txt", excerpt="Atique Ahmed", score=.9)],
        model_id="astra", latency_ms=1)
    repo.save_question_answer(answer)
    assert repo.get_question_answer_history(doc.id)[0].sources == answer.sources
    engine.dispose()


def test_tls_and_wallet_settings_are_passed_without_url_credentials(monkeypatch, tmp_path):
    create = MagicMock(); monkeypatch.setattr("app.distributor_rag.providers.oci.database.create_engine", create)
    cfg = OracleDatabaseSettings(_env_file=None, oci_db_user="RAG_APP", oci_db_password="secret",
        oci_db_dsn="app_low", oci_db_wallet_dir=str(tmp_path), oci_db_wallet_password="wallet-secret")
    assert "wallet-secret" not in repr(cfg) and "'secret'" not in repr(cfg)
    create_oracle_engine(cfg)
    assert create.call_args.args == ("oracle+oracledb://",)
    args = create.call_args.kwargs["connect_args"]
    assert args["ssl_server_dn_match"] is True
    assert args["wallet_location"] == str(tmp_path) and args["wallet_password"] == "wallet-secret"
    assert create.call_args.kwargs["hide_parameters"] is True


@pytest.mark.parametrize("dsn", ["host:1521/service", "tcp://host:1521/service",
    "(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=host)(PORT=1521)))",
    "tcps://host:1521/service?ssl_server_dn_match=false"])
def test_database_configuration_rejects_unencrypted_dsn(dsn):
    with pytest.raises(ValueError):
        OracleDatabaseSettings(_env_file=None, oci_db_user="app", oci_db_password="secret", oci_db_dsn=dsn)


def test_schema_checks_vector_capability_before_creating_tables(monkeypatch):
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value.execute.side_effect = RuntimeError("unsupported")
    create = MagicMock(); monkeypatch.setattr(Base.metadata, "create_all", create)
    with pytest.raises(RuntimeError):
        initialize_schema(engine)
    create.assert_not_called()


def test_ingestion_uses_transaction_lock_native_vectors_and_clob_bind():
    engine, storage, embeddings = MagicMock(), MagicMock(), MagicMock()
    engine.connect.return_value.__enter__.return_value.execute.return_value.scalar.return_value = None
    storage.get_document.return_value = b"Atique Ahmed"
    embeddings.embed.return_value = [[1., 0., 0.]]
    provider = OCIIngestionProvider(storage, engine, embeddings, settings())
    doc = Document(original_filename="facts.txt", storage_key="documents/facts.txt", content_type="text/plain")
    result = provider.start_ingestion(doc)
    assert result.sync_complete
    calls = engine.begin.return_value.__enter__.return_value.execute.call_args_list
    assert "FOR UPDATE" in str(calls[0].args[0]) and "owner_id=:owner" in str(calls[0].args[0])
    chunk = next(c for c in calls if "INSERT INTO distributor_chunks" in str(c.args[0]))
    assert isinstance(chunk.args[1]["vector"], array) and chunk.args[1]["vector"].typecode == "f"
    compiled = chunk.args[0].compile(dialect=oracle.dialect())
    assert str(compiled.binds["content"].type.compile(dialect=oracle.dialect())) == "CLOB"
    assert all("ON CONFLICT" not in str(c.args[0]) for c in calls)
    # A failed insert must exit the transaction with an error, not report successful indexing.
    engine.begin.return_value.__enter__.return_value.execute.side_effect = RuntimeError("write failed")
    with pytest.raises(ProviderError):
        provider.start_ingestion(doc)
    assert engine.begin.return_value.__exit__.call_args.args[0] is RuntimeError


def test_completed_ingestion_retry_does_not_write_or_embed():
    engine, storage, embeddings = MagicMock(), MagicMock(), MagicMock()
    engine.connect.return_value.__enter__.return_value.execute.return_value.scalar.return_value = "digest"
    provider = OCIIngestionProvider(storage, engine, embeddings, settings())
    doc = Document(original_filename="facts.txt", storage_key="documents/facts.txt", content_type="text/plain")
    assert provider.start_ingestion(doc).job_id == "digest"
    embeddings.embed.assert_not_called()
    engine.begin.assert_not_called()


@pytest.mark.skipif(os.environ.get("OCI_RUN_DB_TESTS") != "1", reason="Requires configured Oracle test schema")
def test_live_oracle_vector_checks():
    # Explicit opt-in; uses .env for a prepared schema. All probe inserts are rolled back.
    check = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/check_oci_database.py"))["check_database"]
    engine = create_oracle_engine()
    try:
        check(engine)
    finally:
        engine.dispose()


def test_oci_container_uses_oracle_repository_without_postgres(monkeypatch):
    from app.distributor_rag.core.config import Settings
    from app.distributor_rag.core.dependencies import build_container
    engine, repo, providers = MagicMock(), MagicMock(), MagicMock()
    factory = MagicMock(return_value=(engine, repo))
    monkeypatch.setattr("app.distributor_rag.providers.oci.database.create_oracle_repository", factory)
    postgres = MagicMock(side_effect=AssertionError("Unexpected PostgreSQL connection"))
    monkeypatch.setattr("app.distributor_rag.core.dependencies.create_database", postgres)
    container = build_container(Settings(_env_file=None, cloud_provider="oci"), providers=providers)
    assert container.repository is repo and container.engine is engine
    factory.assert_called_once()
    postgres.assert_not_called()


def test_missing_database_setting_does_not_expose_supplied_password():
    with pytest.raises(ValueError) as error:
        OracleDatabaseSettings(_env_file=None, oci_db_user="app", oci_db_password="do-not-disclose-this")
    assert "do-not-disclose-this" not in str(error.value)
