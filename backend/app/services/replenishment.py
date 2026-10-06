"""Replenishment calculation, recommendation, and case service boundary."""

from typing import Any

from app.enterprise_ai.mcp_safety import EnterpriseAIMCPSafetyClient
from app.oci.database import OracleDatabaseClient
from app.oci.notifications import OCINotificationClient


class ReplenishmentService:
    """Coordinate replenishment recommendations and case transitions."""

    def __init__(
        self,
        database_client: OracleDatabaseClient | None = None,
        notification_client: OCINotificationClient | None = None,
        mcp_safety_client: EnterpriseAIMCPSafetyClient | None = None,
        case_service=None,
    ) -> None:
        self.database_client = database_client or OracleDatabaseClient()
        self.notification_client = notification_client or OCINotificationClient()
        self.mcp_safety_client = mcp_safety_client or EnterpriseAIMCPSafetyClient()
        self.case_service = case_service

    def create_case(self, item_id: str, quantity: int, *, session=None, key=None) -> dict[str, Any]:
        self._require_context(session)
        return self.case_service.create_case(item_id, quantity, session=session, key=key)

    def _require_context(self, session):
        from app.services.cases import CaseService
        CaseService._session(session)
        if self.case_service is None:
            raise RuntimeError("Use the configured application case service")

    def update_case_status(self, case_id: str, status: str) -> bool:
        """Never allow a generic status setter to bypass action-specific evidence."""
        raise PermissionError("Use dedicated case preparation, decision, dispatch, or completion actions")

    def send_supplier_request(self, case_id: str, supplier_id: str, *, session=None) -> dict[str, Any]:
        """Prepare the boundary for a manager-approved supplier notification."""
        self._require_context(session)
        case = self.case_service.get_case(case_id, session=session)
        if case["selected_supplier_id"] != supplier_id:
            raise PermissionError("Selected supplier does not match")
        return self.case_service.dispatch(case_id, session=session, kind="SUPPLIER_REQUEST")

    def send_supplier_response(self, case_id: str, supplier_id: str, *, session=None) -> dict[str, Any]:
        """Prepare the boundary for a supplier-approved retail notification."""
        self._require_context(session)
        session.require_supplier(supplier_id)
        return self.case_service.dispatch(case_id, session=session, kind="SUPPLIER_RESPONSE")

    def close_case(self, case_id: str, *, session=None, key=None) -> dict[str, Any]:
        """Persist completion and approved memory through the transactional service."""
        self._require_context(session)
        return self.case_service.close_case(case_id, session=session, key=key)
