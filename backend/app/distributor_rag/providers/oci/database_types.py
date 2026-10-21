"""Oracle persistence types; domain objects continue to use Python lists and strings."""
import json

from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator


class JSONText(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return json.dumps(value, ensure_ascii=False) if value is not None else None

    def process_result_value(self, value, dialect):
        return json.loads(value) if value is not None else []


class EmptyText(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_result_value(self, value, dialect):
        return value or ""
