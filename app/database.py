"""SQLAlchemy engine, session factory, and database initialization."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Base class for all ORM models."""


def get_db() -> Generator[Session, None, None]:
    """Yield one database session per request and always close it."""

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _migrate_sqlite_explanations() -> None:
    """Add the execution-trace columns to an existing local SQLite database.

    ``create_all`` only creates missing tables; it never alters an existing one.
    The project intentionally avoids a migration framework, so this tiny,
    idempotent upgrade keeps an existing ``linelens.db`` usable. A production
    deployment should use Alembic instead.
    """

    if not settings.database_url.startswith("sqlite"):
        return

    additions = {
        "trace_status": "VARCHAR(16) NOT NULL DEFAULT 'unavailable'",
        "trace_note": "TEXT",
        "final_output": "TEXT NOT NULL DEFAULT ''",
        "trace": "JSON",
    }
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(explanations)")
        }
        for name, ddl in additions.items():
            if name not in existing:
                connection.exec_driver_sql(
                    f"ALTER TABLE explanations ADD COLUMN {name} {ddl}"
                )


def init_db() -> None:
    """Create missing tables and upgrade the local explanation schema.

    This project intentionally avoids a migration framework so it can start with
    one command. A production deployment would normally use Alembic.
    """

    from app import models  # noqa: F401  (registers tables on Base.metadata)

    Base.metadata.create_all(bind=engine)
    _migrate_sqlite_explanations()
