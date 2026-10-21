import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

request_id: ContextVar[str] = ContextVar("request_id", default="startup")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Only allow known structured fields; never serialize exceptions/SDK responses or payloads.
        data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
            "logger": record.name,
            "request_id": request_id.get(),
        }
        for key in ("operation", "latency_ms", "document_id", "status", "error_type", "count"):
            if hasattr(record, key):
                data[key] = getattr(record, key)
        return json.dumps(data)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
    for name in ("botocore", "boto3", "urllib3", "httpx", "httpx2", "sqlalchemy.engine"):
        logging.getLogger(name).setLevel(logging.WARNING)
