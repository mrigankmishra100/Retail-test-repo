from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker


def create_database(url: str):
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def set_foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

    return engine, sessionmaker(engine, expire_on_commit=False)
