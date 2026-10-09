"""Supplier policy location, retrieval, and extraction service boundary."""

from typing import Any
from hashlib import sha256
from io import BytesIO
import logging
from pathlib import Path
import re

from app.oci.storage import MAX_POLICY_BYTES, OCIStorageClient, PolicyStorageError

logger = logging.getLogger(__name__)
DEFAULT_POLICY_DIRECTORY = Path(__file__).resolve().parents[3] / "policies"


class PolicyDocumentError(ValueError):
    """Missing, unreadable, or mismatched policy; no commercial facts inferred."""


def extract_policy_document(document: bytes, supplier_id: str, object_name: str) -> dict[str, Any]:
    """Read the full PDF and verify its supplier and stored filename."""
    from pypdf import PdfReader

    if not isinstance(document, bytes) or not document.startswith(b"%PDF-") or len(document) > MAX_POLICY_BYTES:
        raise PolicyDocumentError("invalid_pdf")
    try:
        reader = PdfReader(BytesIO(document), strict=True)
        if reader.is_encrypted or len(reader.pages) != 1:
            raise PolicyDocumentError("policy_must_be_one_unencrypted_page")
        content = reader.pages[0].extract_text() or ""
    except PolicyDocumentError:
        raise
    except Exception as exc:
        raise PolicyDocumentError("unreadable_pdf") from exc
    # All identity occurrences must agree, including policy ID and footer.
    ids = set(re.findall(r"SUP[0-9]{3}", content))
    if ids != {supplier_id} or object_name not in content:
        raise PolicyDocumentError("policy_identity_mismatch")
    if not content.strip():
        raise PolicyDocumentError("empty_policy")
    return {"content": content, "sha256": sha256(document).hexdigest(), "page_count": 1}


class PolicyService:
    """Retrieve and extract a complete one-page supplier policy PDF."""

    def __init__(self, storage_client: OCIStorageClient | None = None, *,
                 local_directory: Path | None = None) -> None:
        self.storage_client = storage_client or OCIStorageClient()
        self.local_directory = (local_directory or DEFAULT_POLICY_DIRECTORY).resolve()

    def get_supplier_policy(self, supplier_id: str, *, object_name: str | None = None,
                            trusted_supplier_id: str | None = None) -> dict[str, Any]:
        """Prefer Object Storage; fall back to the same supplier's local PDF."""
        if not re.fullmatch(r"SUP[0-9]{3}", supplier_id):
            raise PolicyDocumentError("invalid_supplier_id")
        if trusted_supplier_id is not None and supplier_id != trusted_supplier_id:
            raise PermissionError("Supplier policy access denied")
        expected_name = f"{supplier_id}_policy.pdf"
        if object_name is not None and object_name != expected_name:
            raise PolicyDocumentError("policy_object_mismatch")
        object_name = expected_name
        try:
            document = self.storage_client.get_policy_document(object_name)
            result = extract_policy_document(document, supplier_id, object_name)
            source, fallback_reason = "object_storage", None
        except (PolicyStorageError, PolicyDocumentError) as exc:
            fallback_reason = exc.code if isinstance(exc, PolicyStorageError) else str(exc)
            logger.warning("Policy %s: local fallback (%s)", supplier_id, fallback_reason)
            path = (self.local_directory / object_name).resolve()
            if path.parent != self.local_directory:
                raise PolicyDocumentError("policy_path_outside_local_directory")
            try:
                with path.open("rb") as stream:
                    document = stream.read(MAX_POLICY_BYTES + 1)
                result = extract_policy_document(document, supplier_id, object_name)
            except (OSError, PolicyDocumentError) as local_error:
                raise PolicyDocumentError(
                    f"No usable policy for {supplier_id}: Object Storage {fallback_reason}; local missing or invalid"
                ) from local_error
            source = "local"
        logger.info("Policy %s loaded from %s", supplier_id, source)
        return {**result, "supplier_id": supplier_id, "object_name": object_name,
                "document_loaded": True, "source": source, "fallback_reason": fallback_reason}
