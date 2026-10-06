"""Pure case transition rules; persistence and approval evidence live separately."""

from enum import StrEnum


class CaseStatus(StrEnum):
    DRAFT = "DRAFT"
    AWAITING_MANAGER_APPROVAL = "AWAITING_MANAGER_APPROVAL"
    MANAGER_REJECTED = "MANAGER_REJECTED"
    REQUEST_QUEUED = "REQUEST_QUEUED"
    REQUEST_SENT = "REQUEST_SENT"
    AWAITING_SUPPLIER_APPROVAL = "AWAITING_SUPPLIER_APPROVAL"
    SUPPLIER_REJECTED = "SUPPLIER_REJECTED"
    RESPONSE_QUEUED = "RESPONSE_QUEUED"
    SUPPLIER_RESPONDED = "SUPPLIER_RESPONDED"
    COMPLETED = "COMPLETED"


TRANSITIONS = {
    CaseStatus.DRAFT: frozenset({CaseStatus.AWAITING_MANAGER_APPROVAL}),
    CaseStatus.AWAITING_MANAGER_APPROVAL: frozenset({CaseStatus.MANAGER_REJECTED, CaseStatus.REQUEST_QUEUED}),
    CaseStatus.REQUEST_QUEUED: frozenset({CaseStatus.REQUEST_SENT}),
    CaseStatus.REQUEST_SENT: frozenset({CaseStatus.AWAITING_SUPPLIER_APPROVAL}),
    CaseStatus.AWAITING_SUPPLIER_APPROVAL: frozenset({CaseStatus.SUPPLIER_REJECTED, CaseStatus.RESPONSE_QUEUED}),
    CaseStatus.RESPONSE_QUEUED: frozenset({CaseStatus.SUPPLIER_RESPONDED}),
    CaseStatus.SUPPLIER_RESPONDED: frozenset({CaseStatus.COMPLETED}),
    CaseStatus.MANAGER_REJECTED: frozenset(),
    CaseStatus.SUPPLIER_REJECTED: frozenset(),
    CaseStatus.COMPLETED: frozenset(),
}


def validate_transition(current: str, target: str) -> CaseStatus:
    """Reject unknown, skipped, repeated, and terminal-state transitions."""
    origin, destination = CaseStatus(current), CaseStatus(target)
    if destination not in TRANSITIONS[origin]:
        raise ValueError(f"Invalid case transition: {origin} -> {destination}")
    return destination
