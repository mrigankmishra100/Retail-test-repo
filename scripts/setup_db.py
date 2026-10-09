"""Explicit database setup helper; no connection occurs on import."""

from dataclasses import dataclass
import os
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class DatabaseConfig:
    """Oracle connection settings loaded from environment variables."""

    dsn: str
    user: str
    password: str


def load_database_config() -> DatabaseConfig:
    """Load database settings without opening a connection."""
    return DatabaseConfig(
        dsn=os.getenv("ORACLE_DB_DSN", ""),
        user=os.getenv("ORACLE_DB_USER", ""),
        password=os.getenv("ORACLE_DB_PASSWORD", ""),
    )


def setup_database(config: DatabaseConfig) -> None:
    """Run schema.sql and seed.sql in a future implementation."""
    schema_path = REPOSITORY_ROOT / "database" / "schema.sql"
    seed_path = REPOSITORY_ROOT / "database" / "seed.sql"
    # TODO: Validate config, connect with python-oracledb, and execute both files.
    raise NotImplementedError(f"Database setup is pending: {schema_path}, {seed_path}")


if __name__ == "__main__":
    raise SystemExit("Database setup is a placeholder; implement setup_database before use.")
