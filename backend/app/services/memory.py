"""Short-term state and approved long-term memory service boundary."""

from typing import Any

from app.oci.cache import OCICacheClient
from app.oci.database import OracleDatabaseClient
from app.services.cases import CaseService
from app.contracts import ItemId
from pydantic import TypeAdapter


class MemoryService:
    """Coordinate workflow state and approved long-term case memory.

    OCI Cache stores LangGraph checkpoints and active case state. Oracle
    Database 26ai stores only approved/completed case summaries. Oracle
    Conversation history currently shares the TTL cache; hosted Conversation State
    is separate and pending. Long-term facts are verified against completed approvals.
    """

    def __init__(
        self,
        database_client: OracleDatabaseClient | None = None,
        cache_client: OCICacheClient | None = None,
        case_service=None,
    ) -> None:
        self.database_client = database_client or OracleDatabaseClient()
        self.cache_client = cache_client or OCICacheClient()
        self.case_service = case_service

    def list_approved(self, *, session, item_id=None, limit=10, offset=0):
        CaseService._session(session)
        if item_id is not None:
            item_id = TypeAdapter(ItemId).validate_python(item_id)
        if type(limit) is not int or not 1 <= limit <= 20 or type(offset) is not int or not 0 <= offset <= 10000:
            raise ValueError("Invalid memory pagination")
        if self.case_service is None:
            raise RuntimeError("Configured case service is required for memory evidence")
        rows = self.database_client.fetch_all("approved_memory_page", {"supplier_id": session.supplier_id,
            "item_id": item_id, "offset": offset, "page_size": limit + 1})
        # All selected memories are independently checked against completed approvals/deliveries.
        items = [self.case_service.approved_memory(row["case_id"], session=session) for row in rows[:limit]]
        return {"items": items, "limit": limit, "offset": offset, "has_more": len(rows) > limit}

    def save_session_state(self, session_id: str, state: dict[str, Any]) -> None:
        """Store checkpoint data in a namespace separate from session identity."""
        from app.oci.cache import CacheUnavailable
        if not self.cache_client.set_state(f"workflow:{session_id}", state):
            raise CacheUnavailable("Session state was not saved")

    def get_session_state(self, session_id: str) -> dict[str, Any] | None:
        return self.cache_client.get_state(f"workflow:{session_id}")

    def save_approved_summary(self, case_id: str, summary: str) -> None:
        """Reject arbitrary memory writes; completion owns the atomic summary insert."""
        raise PermissionError("Summaries are derived from stored approvals by CaseService.close_case")
