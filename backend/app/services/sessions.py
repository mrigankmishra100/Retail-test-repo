"""Development POC session issuance and immutable server-side role context."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import re
from secrets import token_urlsafe
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, model_validator, ValidationError

from app.oci.cache import OCICacheClient
from app.oci.database import OracleDatabaseClient

Role = Literal["retail-manager", "supplier"]


class SessionError(PermissionError):
    """Unknown, expired, or invalid bearer session."""


class SessionContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    session_id: UUID
    role: Role
    supplier_id: str | None = None
    expires_at: datetime

    @model_validator(mode="after")
    def validate_identity(self):
        if self.expires_at.tzinfo is None:
            raise ValueError("Session expiry requires a timezone")
        if self.role == "supplier":
            if not self.supplier_id or not re.fullmatch(r"SUP[0-9]{3}", self.supplier_id):
                raise ValueError("Supplier session requires one valid supplier ID")
        elif self.supplier_id is not None:
            raise ValueError("Retail session cannot have a supplier identity")
        return self

    def require_role(self, role: Role) -> None:
        if self.role != role:
            raise PermissionError("Action not allowed for this session role")

    def require_supplier(self, supplier_id: str) -> None:
        self.require_role("supplier")
        if self.supplier_id != supplier_id:
            raise PermissionError("Cross-supplier access denied")


class SessionService:
    def __init__(self, cache: OCICacheClient, database: OracleDatabaseClient, *,
                 ttl_seconds: int = 3600, development: bool = True,
                 now=lambda: datetime.now(timezone.utc)) -> None:
        self.cache, self.database = cache, database
        self.ttl_seconds, self.development, self.now = ttl_seconds, development, now

    @staticmethod
    def _key(token: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise SessionError("Invalid or expired session")
        return "identity:" + sha256(token.encode()).hexdigest()

    def create(self, role: Role, supplier_id: str | None = None) -> dict:
        if not self.development:
            raise PermissionError("POC role selection is disabled outside development")
        session = SessionContext(session_id=uuid4(), role=role, supplier_id=supplier_id,
                                 expires_at=self.now() + timedelta(seconds=self.ttl_seconds))
        if role == "supplier" and not self.database.fetch_all("supplier_identity", {"supplier_id": supplier_id}):
            raise ValueError("Unknown supplier")
        token = token_urlsafe(32)
        if not self.cache.set_state(self._key(token), session.model_dump(mode="json"),
                                    ttl_seconds=self.ttl_seconds, only_if_absent=True):
            raise RuntimeError("Could not reserve session token")
        return {**session.model_dump(mode="json"), "session_token": token}

    def resolve(self, token: str) -> SessionContext:
        value = self.cache.get_state(self._key(token))
        if value is None:
            raise SessionError("Invalid or expired session")
        try:
            session = SessionContext.model_validate(value)
        except ValidationError as exc:
            raise SessionError("Invalid or expired session") from exc
        if session.expires_at <= self.now():
            self.cache.delete_state(self._key(token))
            raise SessionError("Invalid or expired session")
        return session

    def revoke(self, token: str) -> None:
        self.cache.delete_state(self._key(token))

    def from_authorization(self, authorization: str | None) -> SessionContext:
        parts = (authorization or "").split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise SessionError("Bearer session required")
        return self.resolve(parts[1])
