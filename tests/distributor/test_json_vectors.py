"""Oracle 19c compatibility stays scoped to Distributor-owned demo knowledge."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import Text

from app.distributor_rag.domain.errors import ProviderError
from app.distributor_rag.domain.models import Document, SearchFilter
from app.distributor_rag.providers.oci.common import index_identity
from app.distributor_rag.providers.oci.database import initialize_schema
from app.distributor_rag.providers.oci.database_types import JSONText
from app.distributor_rag.providers.oci.ingestion import OCIIngestionProvider
from app.distributor_rag.providers.oci.vector_search import OCIVectorSearchProvider, bounded_text, unit_vector


def settings():
    return SimpleNamespace(oci_embedding_model_id='model', oci_embedding_endpoint='https://model.oci.oraclecloud.com',
        oci_embedding_serving_mode='on_demand', oci_embedding_dimensions=3,
        oci_chunk_chars=200, oci_chunk_overlap=20, oci_vector_storage='json')


def provider(rows):
    engine, embedding = MagicMock(), MagicMock()
    embedding.embed.return_value = [[1., 0., 0.]]
    conn = engine.connect.return_value.__enter__.return_value
    conn.execute.return_value.mappings.return_value = rows
    return OCIVectorSearchProvider(engine, embedding, settings()), conn


def row(name, vector):
    return {'chunk_id': name, 'document_id': 'document', 'content': 'A synthetic fact',
            'page': None, 'embedding': json.dumps(vector)}


def test_json_retrieval_correct_cosine_order_and_ties_with_sql_scope():
    search, conn = provider([row('negative', [-1., 0., 0.]), row('z', [1., 0., 0.]),
                             row('middle', [1., 1., 0.]), row('a', [3., 0., 0.])])
    results = search.retrieve_documents('question', SearchFilter(document_id='document', owner_id='owner'), 3)
    assert [r.source_id for r in results] == ['a', 'z', 'middle']
    assert results[0].score == 1
    assert results[2].score == pytest.approx(2 ** -.5)
    statement, params = conn.execute.call_args.args
    assert 'VECTOR_DISTANCE' not in str(statement)
    assert 'JSON_SERIALIZE(embedding RETURNING CLOB) AS embedding' in str(statement)
    assert 'document_id=:document AND owner_id=:owner AND index_id=:index' in str(statement)
    assert params == {'document': 'document', 'owner': 'owner', 'index': search.index_id, 'row_limit': 1501}


@pytest.mark.parametrize('vector', [[1., 0.], [0., 0., 0.], [True, 0, 0], [float('nan'), 1, 0],
    [float('inf'), 1, 0], ['1', 0, 0], {'0': 1, '1': 0, '2': 0}])
def test_invalid_stored_vectors_fail_closed(vector):
    search, _ = provider([row('bad', vector)])
    with pytest.raises(ProviderError):
        search.retrieve_documents('question', SearchFilter(document_id='document', owner_id='owner'), 3)


def test_large_knowledge_is_not_silently_truncated():
    search, _ = provider([row(str(n), [1, 0, 0]) for n in range(1501)])
    with pytest.raises(ProviderError, match='1500-chunk'):
        search.retrieve_documents('question', SearchFilter(document_id='document', owner_id='owner'), 3)


def test_clob_read_is_bounded_and_uses_oracle_argument_order():
    lob = MagicMock()
    lob.read.return_value = 'abc'
    assert bounded_text(lob, 10) == 'abc'
    lob.read.assert_called_once_with(offset=1, amount=11)
    with pytest.raises(ProviderError):
        bounded_text('x' * 11, 10)


def test_serialized_json_lob_retrieval_is_bounded_before_connection_closes():
    embedding, content = MagicMock(), MagicMock()
    embedding.read.return_value = '[1, 0, 0]'
    content.read.return_value = 'Synthetic carrier information'
    record = row('lob', [1, 0, 0])
    record.update(embedding=embedding, content=content)
    search, _ = provider([record])
    result = search.retrieve_documents('question', SearchFilter(document_id='document', owner_id='owner'), 1)
    assert result[0].score == 1 and result[0].text == content.read.return_value
    embedding.read.assert_called_once_with(offset=1, amount=1_000_001)
    content.read.assert_called_once_with(offset=1, amount=12001)


def test_storage_mode_changes_index_identity_without_changing_native_identity():
    cfg = settings()
    json_id = index_identity(cfg)
    cfg.oci_vector_storage = 'native'
    native_id = index_identity(cfg)
    del cfg.oci_vector_storage
    assert index_identity(cfg) == native_id != json_id


def test_json_setup_uses_clob_not_vector_and_never_retail_ddl(monkeypatch):
    from app.distributor_rag.providers.oci.database_models import Base
    create = MagicMock()
    monkeypatch.setattr(Base.metadata, 'create_all', create)
    engine = MagicMock()
    conn = engine.connect.return_value.__enter__.return_value
    conn.execute.return_value.scalar_one.return_value = 0
    initialize_schema(engine, vector_storage='json')
    statements = [str(c.args[0]) for c in conn.execute.call_args_list]
    assert any('embedding CLOB NOT NULL CHECK (embedding IS JSON)' in s for s in statements)
    assert not any('VECTOR' in s or 'DROP ' in s for s in statements)
    assert set(Base.metadata.tables) == {'distributor_documents', 'distributor_answers', 'distributor_ingestion_locks',
                                        'distributor_releases', 'distributor_active_knowledge'}
    assert create.call_count == 1


def test_invalid_setup_mode_stops_before_any_ddl():
    engine = MagicMock()
    with pytest.raises(ValueError):
        initialize_schema(engine, vector_storage='surprise')
    engine.connect.assert_not_called()


def test_json_ingestion_binds_embeddings_as_clob():
    storage, engine, embeddings = MagicMock(), MagicMock(), MagicMock()
    engine.connect.return_value.__enter__.return_value.execute.return_value.scalar.return_value = None
    storage.get_document.return_value = b'A small synthetic document.'
    embeddings.embed.return_value = [[.1, .2, .3]]
    document = Document(original_filename='demo.txt', storage_key='distributor/demo.txt', content_type='text/plain')
    service = OCIIngestionProvider(storage, engine, embeddings, settings())
    service.start_ingestion(document)
    calls = engine.begin.return_value.__enter__.return_value.execute.call_args_list
    insert = next(c for c in calls if 'INSERT INTO distributor_chunks' in str(c.args[0]))
    statement, params = insert.args
    assert isinstance(statement._bindparams['vector'].type, Text)
    assert json.loads(params['vector']) == [.1, .2, .3]
    assert params['document'] == document.id and params['owner'] == document.owner_id
    assert all('retail' not in str(c.args[0]).lower() for c in calls)


def test_json_mode_does_not_adopt_native_or_unprepared_columns():
    engine = MagicMock()
    conn = engine.connect.return_value.__enter__.return_value
    conn.execute.return_value.first.return_value = (2,)
    conn.execute.return_value.scalar.return_value = 'VECTOR'
    service = OCIIngestionProvider(MagicMock(), engine, MagicMock(), settings())
    with pytest.raises(ProviderError, match='does not match'):
        service.validate_configuration()


@pytest.mark.parametrize('native', [True, False])
def test_answer_sources_accept_native_or_text_json_results(native):
    references = [{'source_id': 'chunk-1', 'excerpt': 'Synthetic carrier information'}]
    value = references if native else json.dumps(references)
    assert JSONText().process_result_value(value, None) == references
    assert JSONText().process_result_value(None, None) == []
