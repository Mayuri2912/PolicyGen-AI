"""Database engine and session management.

SQLite is the default.  The URL comes from configuration, and the models use
only portable SQLAlchemy types, so pointing DATABASE_URL at PostgreSQL needs no
code change.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker, with_loader_criteria

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _session_factory
    if _engine is None:
        url = get_settings().resolved_database_url
        is_sqlite = url.startswith("sqlite")
        connect_args = {"check_same_thread": False, "timeout": 30} if is_sqlite else {}
        if not is_sqlite:
            import re

            schema = get_settings().database_schema
            if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
                raise ValueError("Invalid database schema.")
            connect_args = {"connect_timeout": 15}
        pool_options = {} if is_sqlite else {"pool_size": 3, "max_overflow": 2, "pool_recycle": 300}
        _engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True, **pool_options)
        if is_sqlite:

            @event.listens_for(_engine, "connect")
            def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - driver hook
                cursor = dbapi_connection.cursor()
                # WAL lets the background workers write while requests read.
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.close()

        else:

            @event.listens_for(_engine, "connect")
            def _postgres_schema(dbapi_connection, _record):
                # Session poolers can discard startup options; set this on
                # the actual connection outside a transaction instead.
                previous_autocommit = dbapi_connection.autocommit
                dbapi_connection.autocommit = True
                try:
                    with dbapi_connection.cursor() as cursor:
                        cursor.execute(f'SET SESSION search_path TO "{schema}"')
                finally:
                    dbapi_connection.autocommit = previous_autocommit

        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    return _engine


def init_db() -> None:
    """Create all tables.  Safe to call repeatedly."""
    from app.models import entities  # noqa: F401  (registers the models)

    engine = get_engine()
    # Additive migration: retain all existing rows in the original workspace.
    inspector = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if "workspace_id" in table.c and inspector.has_table(table.name) and "workspace_id" not in {c["name"] for c in inspector.get_columns(table.name)}:
            with engine.begin() as connection:
                connection.execute(text(f"ALTER TABLE \"{table.name}\" ADD COLUMN workspace_id VARCHAR(32) NOT NULL DEFAULT 'default'"))
                connection.execute(text(f'CREATE INDEX "ix_{table.name}_workspace_id" ON "{table.name}" (workspace_id)'))
    Base.metadata.create_all(bind=engine)


def reset_engine() -> None:
    """Dispose the engine so the next call re-reads configuration (tests)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


def new_session() -> Session:
    get_engine()
    assert _session_factory is not None
    return _session_factory()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for background workers."""
    session = new_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    session = new_session()
    try:
        yield session
    finally:
        session.close()


@event.listens_for(Session, "do_orm_execute")
def _isolate_workspace(execute_state):
    from app.core.identity import workspace
    from app.models.entities import WorkspaceRecord
    selected = workspace.get()
    if selected is not None and execute_state.is_select:
        execute_state.statement = execute_state.statement.options(
            with_loader_criteria(WorkspaceRecord, lambda cls: cls.workspace_id == selected, include_aliases=True))


@event.listens_for(Session, "before_flush")
def _stamp_workspace(session, flush_context, instances):
    from app.core.identity import workspace
    from app.models.entities import WorkspaceRecord
    selected = workspace.get()
    if selected is not None:
        for row in session.new:
            if isinstance(row, WorkspaceRecord):
                row.workspace_id = selected
