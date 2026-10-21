from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from app.distributor_rag.domain.errors import ProviderError
from app.distributor_rag.domain.models import Document, SearchFilter
from app.distributor_rag.providers.oci.ingestion import chunk_text
from app.distributor_rag.providers.oci.embeddings import OCIEmbeddings
from app.distributor_rag.providers.oci.storage import OCIStorageProvider
from app.distributor_rag.providers.oci.vector_search import OCIVectorSearchProvider
from app.distributor_rag.providers.oci.common import index_identity


def settings():
    return SimpleNamespace(oci_namespace="namespace", oci_bucket="bucket", object_storage_prefix="documents/",
        oci_embedding_model_id="model-a", oci_embedding_endpoint="endpoint-a", oci_embedding_serving_mode="on_demand",
        oci_embedding_dimensions=3, oci_embedding_set_dimensions=False,
        oci_compartment_id="compartment", oci_chunk_chars=200, oci_chunk_overlap=20)


def test_embedding_dimensions_fail_closed():
    client = MagicMock()
    client.embed_text.return_value.data.embeddings = [[1.0, 2.0]]
    with pytest.raises(ProviderError):
        OCIEmbeddings(client, settings()).embed(["text"], "SEARCH_DOCUMENT")


def test_model_change_uses_new_index():
    config = settings()
    first = index_identity(config)
    config.oci_embedding_model_id = "model-b"
    assert first != index_identity(config)


def test_chunks_preserve_text_and_overlap():
    assert chunk_text("abcdefghij", 5, 2) == ["abcde", "defgh", "ghij"]
    with pytest.raises(ValueError):
        chunk_text("text", 5, 5)


def test_storage_preserves_document_metadata():
    client = MagicMock()
    doc = Document(original_filename="test.txt", storage_key="documents/test.txt", content_type="text/plain")
    provider = OCIStorageProvider(client, settings())
    result = provider.upload_document(doc, b"hello")
    assert result.key == doc.storage_key
    assert client.put_object.call_args.kwargs["opc_meta"] == {"document_id": doc.id, "owner_id": doc.owner_id}


def test_retrieval_filters_in_sql_and_normalizes_sources():
    engine, embeddings = MagicMock(), MagicMock()
    embeddings.embed.return_value = [[1.0, 0.0, 0.0]]
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.mappings.return_value.all.return_value = [
        {"chunk_id": "source", "document_id": "doc", "content": "A fact", "page": None, "score": .9}]
    provider = OCIVectorSearchProvider(engine, embeddings, settings())
    chunks = provider.retrieve_documents("Question", SearchFilter(document_id="doc", owner_id="owner"), 3)
    statement, params = connection.execute.call_args.args
    assert "document_id=:document" in str(statement) and "owner_id=:owner" in str(statement)
    assert "index_id=:index" in str(statement)
    assert params["document"] == "doc" and params["owner"] == "owner"
    assert chunks[0].document_id == "doc" and chunks[0].text == "A fact"


@pytest.mark.parametrize("api_format", ["generic", "generic_astra"])
def test_generic_and_astra_answer_request_contract(api_format):
    import json
    import oci
    from app.distributor_rag.providers.oci.llm import OCILLMProvider
    m = oci.generative_ai_inference.models
    client = MagicMock()
    client.chat.return_value.data = m.ChatResult(chat_response=m.GenericChatResponse(
        choices=[m.ChatChoice(index=0, finish_reason="stop", message=m.AssistantMessage(
            content=[m.TextContent(text="Atique Ahmed")]))]))
    cfg = settings()
    cfg.oci_llm_format = api_format
    cfg.oci_llm_model_id = "test-model"
    cfg.oci_llm_serving_mode = "on_demand"
    result = OCILLMProvider(client, cfg, {}).generate_response(
        "Answer using the sources.", "What is the full name of Atique?",
        [{"text": "Atique Ahmed"}], SimpleNamespace(max_tokens=2048, temperature=0.1))
    assert result.text == "Atique Ahmed"
    detail = client.chat.call_args.args[0]
    req = detail.chat_request
    assert detail.serving_mode.model_id == "test-model"
    assert req.api_format == "GENERIC"
    assert req.messages[0].content[0].text == "Answer using the sources."
    assert json.loads(req.messages[1].content[0].text)["retrieved_sources"] == [{"text": "Atique Ahmed"}]
    if api_format == "generic_astra":
        assert req.max_completion_tokens == 2048 and req.verbosity == "MEDIUM"
        assert req.max_tokens is None and req.temperature is None
    else:
        assert req.max_tokens == 2048 and req.temperature == 0.1
        assert req.max_completion_tokens is None


def test_astra_incomplete_answer_is_not_presented_as_success():
    from app.distributor_rag.providers.oci.llm import OCILLMProvider
    client = MagicMock()
    client.chat.return_value.data.chat_response.choices = [
        SimpleNamespace(finish_reason="length", message=SimpleNamespace(content=[]))]
    cfg = settings()
    cfg.oci_llm_format = "generic_astra"
    cfg.oci_llm_model_id = "test-model"
    cfg.oci_llm_serving_mode = "on_demand"
    with pytest.raises(ProviderError, match="incomplete"):
        OCILLMProvider(client, cfg, {}).generate_response(
            "system", "question", [], SimpleNamespace(max_tokens=2048, temperature=0))


def test_compatible_model_reuses_resource_principal_signer(monkeypatch):
    from app.distributor_rag.providers.oci.llm import OCILLMProvider
    cfg = settings()
    cfg.oci_llm_format, cfg.oci_llm_model_id = 'responses', 'fixture-model'
    cfg.oci_llm_endpoint = 'https://inference.generativeai.us-ashburn-1.oci.oraclecloud.com/v1'
    signer, post = MagicMock(), MagicMock()
    post.return_value.status_code = 200
    post.return_value.json.return_value = {'output':[{'content':[{'type':'output_text','text':'Fixture answer'}]}]}
    monkeypatch.setattr('app.distributor_rag.providers.oci.llm.requests.post', post)
    monkeypatch.setattr('oci.signer.Signer', MagicMock(side_effect=AssertionError('No user key')))
    result = OCILLMProvider(MagicMock(), cfg, {}, signer=signer).generate_response(
        'system', 'question', 'evidence', SimpleNamespace(max_tokens=100))
    assert result.text == 'Fixture answer'
    assert post.call_args.kwargs['auth'] is signer
    assert post.call_args.kwargs['allow_redirects'] is False
    assert post.call_args.kwargs['json']['store'] is False
