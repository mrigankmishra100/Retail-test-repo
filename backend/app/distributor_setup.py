"""Explicit preparation entry point; never called by login, startup or chat.

Plan is read-only. --apply creates only the seven namespaced Distributor tables
in a schema with no existing Distributor tables. It never seeds retail data,
drops objects, adopts unknown tables or runs repository-supplied SQL.
"""
import argparse
import json

from sqlalchemy import inspect

from app.distributor_rag.providers.oci.database import VECTOR_TABLES, initialize_schema
from app.distributor_rag.providers.oci.database_models import Base


def prepare(engine, *, apply=False):
    expected = set(Base.metadata.tables) | set(VECTOR_TABLES)
    existing = {name.lower() for name in inspect(engine).get_table_names()}
    found = sorted(expected & existing)
    if apply:
        if found:
            raise ValueError('Distributor tables already exist. Refusing to adopt or overwrite them; review their schema and ownership first.')
        initialize_schema(engine)
    return {'mode': 'applied' if apply else 'plan', 'tables': sorted(expected),
            'already_present': found, 'retail_tables_changed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Create the listed tables; Oracle DDL commits immediately')
    args = parser.parse_args()
    from app.dependencies import get_container
    application = get_container()
    try:
        context = application.distributor_service.context()
        print(json.dumps(prepare(context.engine, apply=args.apply)))
    except Exception:
        parser.exit(1, 'Distributor setup failed or found existing tables. No retail tables are changed by this tool. Review access, vector support and any partially created Distributor tables.\n')
    finally:
        application.close()


if __name__ == '__main__':
    main()
