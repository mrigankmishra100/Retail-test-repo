import json
import logging
import re
from itertools import zip_longest
from time import perf_counter
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.distributor_rag.domain.errors import AppError, ProviderError
from app.distributor_rag.domain.models import (
    Document,
    Generation,
    InferenceConfig,
    QuestionAnswer,
    SearchChunk,
    SearchFilter,
    Source,
    Status,
)
from app.distributor_rag.interfaces.llm import LLMProvider
from app.distributor_rag.interfaces.repository import Repository
from app.distributor_rag.interfaces.vector_search import VectorSearchProvider
from app.distributor_rag.services.distributors import remove_legacy_demo_prefix, retrieval_queries
from app.distributor_rag.workflows.prompts import SYSTEM_PROMPT

log = logging.getLogger(__name__)
INSUFFICIENT = "The document does not contain enough information to answer this question."


class RAGState(TypedDict, total=False):
    document_id: str
    question: str
    document: Document
    chunks: list[SearchChunk]
    sources: list[Source]
    context: str
    generation: Generation
    result: QuestionAnswer
    started: float
    demo_data: bool


def build_rag_graph(
    llm: LLMProvider,
    vector: VectorSearchProvider,
    repository: Repository,
    config: InferenceConfig,
    top_k: int = 5,
    max_context_chars: int = 24000,
    max_question_chars: int = 4000,
    distributor_mode: bool = False,
):
    def validate(state: RAGState):
        question = state["question"].strip()
        if not question or len(question) > max_question_chars:
            raise AppError("Enter a nonempty question within the length limit.", "invalid_question", 422)
        document = repository.get_document(state["document_id"])
        if document.ingestion_status != Status.READY:
            raise AppError("Wait until the document is indexed and searchable.", "document_not_ready", 409)
        return {"document": document, "question": question, "started": perf_counter()}

    def retrieve(state: RAGState):
        doc = state["document"]
        queries = retrieval_queries(state["question"]) if distributor_mode else [state["question"]]
        groups = [vector.retrieve_documents(
            query, SearchFilter(document_id=doc.id, owner_id=doc.owner_id), top_k
        ) for query in queries]
        # Interleave results so a comparison has evidence from every query before truncation.
        chunks = [chunk for row in zip_longest(*groups) for chunk in row if chunk is not None]
        if any(chunk.document_id != doc.id for chunk in chunks):
            raise ProviderError("The search index returned inconsistent document metadata.")
        log.info("context_retrieved", extra={"document_id": doc.id, "count": len(chunks)})
        return {"chunks": chunks}

    def prompt(state: RAGState):
        remaining, references, sources = max_context_chars, [], []
        seen = set()
        for chunk in state["chunks"]:
            if remaining <= 0:
                break
            if chunk.source_id in seen or not chunk.text.strip():
                continue
            seen.add(chunk.source_id)
            excerpt = chunk.text[:remaining]
            remaining -= len(excerpt)
            source_id = f"source-{len(sources) + 1}"
            sources.append(
                Source(
                    source_id=source_id,
                    document_name=state["document"].original_filename,
                    excerpt=excerpt,
                    score=chunk.score,
                    page=chunk.metadata.get("page"),
                )
            )
            references.append({"source_id": source_id, "text": excerpt})
        return {"context": json.dumps(references, ensure_ascii=False), "sources": sources}

    def generate(state: RAGState):
        if not state["sources"]:
            log.info("answer_without_context", extra={"document_id": state["document_id"]})
            return {"generation": Generation(text=INSUFFICIENT, model_id="no-context")}
        system = SYSTEM_PROMPT
        if distributor_mode:
            system += "\nYou are Distributor Aggregator. Distinguish BlueDart, Delhivery, Ekart and DTDC. Compare only documented attributes using identical units and conditions. An omitted attribute means not documented, not unsupported. If evidence for one company is missing, explicitly state that gap. Do not invent current prices, shipment status or real-world policies."
            system += "\nA reference may span several distributors. Attribute each fact ONLY to the distributor explicitly named in that fact or its section. Never transfer a number or condition from one distributor to another. For comparisons, write a separate labelled paragraph per distributor, naming the exact service and citing its own supporting passages. Before responding, check each distributor, number, unit and condition against the cited text. Economy Parcel and Priority Surface are service names; report those names rather than inventing a service called Standard."
            if state.get("demo_data"):
                system += "\nAll reference facts are fictional demo data. Answer directly from the supplied reference facts, without a demo disclaimer or introductory label. Do not claim that the reference facts have been independently verified."
        result = llm.generate_response(system, state["question"], state["context"], config)
        allowed = {source.source_id for source in state["sources"]}
        citation_groups = re.findall(r"\[(source-[^\]]*)\]", result.text)
        cited = {item.strip() for group in citation_groups for item in group.split(",")}
        # Fail closed on invented IDs and uncited assertions; citations never come from model-generated metadata.
        if not cited or not cited.issubset(allowed):
            log.warning(
                "answer_citation_validation_failed",
                extra={
                    "document_id": state["document_id"],
                    "error_type": "missing_citations" if not cited else "unknown_citation",
                    "count": len(state["sources"]),
                },
            )
            result = result.model_copy(update={"text": INSUFFICIENT})
            sources = []
        else:
            # Models sometimes combine citations; normalize them to the UI's individual IDs.
            result = result.model_copy(update={"text": re.sub(
                r"\[(source-[^\]]*)\]",
                lambda match: " ".join(f"[{item.strip()}]" for item in match.group(1).split(",")),
                result.text,
            )})
            sources = [s for s in state["sources"] if s.source_id in cited]
        return {"generation": result, "sources": sources}

    def save(state: RAGState):
        answer_text = state["generation"].text
        if distributor_mode:
            answer_text = remove_legacy_demo_prefix(answer_text)
        result = repository.save_question_answer(
            QuestionAnswer(
                document_id=state["document_id"],
                question=state["question"],
                answer=answer_text,
                sources=state["sources"],
                model_id=state["generation"].model_id,
                latency_ms=int((perf_counter() - state["started"]) * 1000),
            )
        )
        return {"result": result}

    graph = StateGraph(RAGState)
    for name, node in (
        ("validate", validate),
        ("retrieve", retrieve),
        ("build_prompt", prompt),
        ("generate", generate),
        ("save", save),
    ):
        graph.add_node(name, node)
    for before, after in zip(
        (START, "validate", "retrieve", "build_prompt", "generate", "save"),
        ("validate", "retrieve", "build_prompt", "generate", "save", END),
    ):
        graph.add_edge(before, after)
    return graph.compile()
