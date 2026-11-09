"""Database engine and session management.

PostgreSQL is the production database. A local SQLite file is used as a
fallback for development and automated tests when DATABASE_URL is not set.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from config import get_settings

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def _normalise_url(url: str) -> str:
    # Managed providers often hand out postgres:// URLs.
    if url.startswith("postgres://"):
        return "postgresql+psycopg2://" + url[len("postgres://"):]
    return url


def build_engine(url: str) -> Engine:
    url = _normalise_url(url)
    if url.startswith("sqlite"):
        kwargs: dict = {"connect_args": {"check_same_thread": False}}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool
        engine = create_engine(url, future=True, **kwargs)

        file_db = url not in ("sqlite://", "sqlite:///:memory:")

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")  # enforce foreign keys on SQLite
            if file_db:
                cur.execute("PRAGMA journal_mode=WAL")  # readers don't block writers
                cur.execute("PRAGMA busy_timeout=5000")
            cur.close()

        return engine
    return create_engine(url, future=True, pool_pre_ping=True, pool_size=5, max_overflow=5)


def configure(url: str | None = None) -> Engine:
    """(Re)configure the global engine. Tests call this with an in-memory URL."""
    global _engine, _SessionLocal
    _engine = build_engine(url or get_settings().database_url)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        configure()
    assert _engine is not None
    return _engine


def get_sessionmaker() -> sessionmaker:
    if _SessionLocal is None:
        configure()
    assert _SessionLocal is not None
    return _SessionLocal


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on any error."""
    session: Session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(create_all: bool = True) -> None:
    """Create tables (dev/test convenience) and seed reference data.

    Production deployments should run `alembic upgrade head` instead and then
    call `seed_reference_data` (done by scripts/bootstrap_admin.py).
    """
    from database.models import Base
    from database.seed import seed_reference_data

    engine = get_engine()
    if create_all:
        Base.metadata.create_all(engine)
    with session_scope() as s:
        seed_reference_data(s)
