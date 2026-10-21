"""Local administrator ingestion; never expose this command as an unauthenticated API."""
import argparse
import hashlib
import json
import re
import time
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from app.distributor_rag.domain.errors import AppError, ProviderError
from app.distributor_rag.domain.models import KnowledgeRelease, SearchFilter, Status
from app.distributor_rag.services.distributors import ALIASES
from app.distributor_rag.services.documents import validate_document


def ingest(container, path: Path, version: str, demo: bool, timeout: int = 900, poll_seconds: float = 5):
    content = path.read_bytes()
    validate_document(path.name, content, container.settings.max_upload_bytes)
    digest = hashlib.sha256(content).hexdigest()
    release = container.repository.get_release_by_hash(digest)
    if release and release.demo_data != demo:
        raise AppError("The same content cannot be relabelled between demo and approved data.")
    if release is None:
        identifier = str(uuid5(NAMESPACE_URL, "distributor-knowledge:" + digest))
        try:
            document = container.repository.get_document(identifier)
        except AppError as exc:
            if exc.status_code != 404:
                raise
            document = container.documents.upload(path.name, content, document_id=identifier)
        release = container.repository.register_release(KnowledgeRelease(
            document_id=document.id, content_sha256=digest, version=version, demo_data=demo,
        ))
    deadline = time.monotonic() + timeout
    while True:
        try:
            document = container.documents.refresh(release.document_id)
            if document.ingestion_status == Status.FAILED:
                raise AppError(document.status_message, "ingestion_failed", 409)
            if document.ingestion_status == Status.READY:
                # A sync-complete response alone is insufficient. Confirm all four topics are searchable.
                for name, pattern in ALIASES.items():
                    chunks = container.providers.vector.retrieve_documents(
                        name + " services coverage delivery returns", SearchFilter(document_id=document.id, owner_id=document.owner_id), 5,
                    )
                    if not any(re.search(pattern, c.text, re.I) for c in chunks):
                        raise ProviderError("Waiting for all distributor sections to become searchable.")
                return container.repository.activate_release(document.id)
        except ProviderError:
            pass  # Transient provider errors can be retried without another upload.
        if time.monotonic() >= deadline:
            raise AppError("Ingestion verification timed out. Rerun the same command to resume; the previous active version is unchanged.", "ingestion_timeout", 503)
        time.sleep(poll_seconds)


def main():
    parser = argparse.ArgumentParser(description="Manage Distributor Aggregator knowledge releases")
    commands = parser.add_subparsers(dest="command", required=True)
    upload = commands.add_parser("ingest")
    upload.add_argument("file", type=Path)
    upload.add_argument("--version", required=True)
    upload.add_argument("--demo", action="store_true", help="Mark all resulting answers as fictional demo data")
    upload.add_argument("--timeout", type=int, default=900)
    activate = commands.add_parser("activate", help="Switch to an already verified release, including rollback")
    activate.add_argument("document_id")
    commands.add_parser("status")
    args = parser.parse_args()
    if args.command == "ingest" and (not args.version.strip() or len(args.version) > 100 or args.timeout < 1):
        parser.error("Use a version of 1-100 characters and a positive timeout.")
    # Use the merged backend's private configuration, resource principal and DB
    # pool. Never fall back to the upstream standalone AWS defaults.
    from app.dependencies import get_container
    application = get_container()
    try:
        container = application.distributor_service.context()
        if args.command == "ingest":
            release = ingest(container, args.file, args.version, args.demo, args.timeout)
        elif args.command == "activate":
            release = container.repository.activate_release(args.document_id)
        else:
            release = container.repository.get_active_release()
        print(json.dumps(release.model_dump(mode="json") if release else {"ready": False}))
    except AppError as exc:
        parser.exit(1, exc.message + "\n")
    except Exception:
        parser.exit(1, 'Distributor operation failed. Review private model configuration, DB setup and OCI access.\n')
    finally:
        application.close()


if __name__ == "__main__":
    main()
