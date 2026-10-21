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
        if value is None:
            return []
        # python-oracledb can decode IS JSON CLOB columns before SQLAlchemy's
        # result processor runs. Do not try to JSON-decode an existing list.
        if hasattr(value, 'read'):
            value = value.read()
        return json.loads(value) if isinstance(value, (str, bytes, bytearray)) else value


class EmptyText(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_result_value(self, value, dialect):
        return value or ""
