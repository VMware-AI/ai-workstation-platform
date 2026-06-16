"""Async SQLAlchemy session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings


def make_engine(url: str | None = None):
    engine = create_async_engine(url or get_settings().database_url, future=True, echo=False)
    # SQLite ships with FK enforcement OFF per-connection; without this the
    # ON DELETE / FK constraints silently no-op and orphan rows insert
    # cleanly (PR-review #81). Enable it on every pooled connection.
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine.sync_engine, "connect")
        def _enable_sqlite_fk(dbapi_conn, _record):  # pragma: no cover - thin glue
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine, _sessionmaker
    if _engine is None:
        _engine = make_engine()
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    return _engine


async def get_session() -> AsyncIterator[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None  # nosec B101 — import-time invariant, not user input
    async with _sessionmaker() as session:
        yield session


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the process sessionmaker for callers that must own their own
    short transactions (e.g. work that releases the request txn before slow IO).
    """
    get_engine()
    assert _sessionmaker is not None  # nosec B101 — import-time invariant, not user input
    return _sessionmaker


def reset_for_tests() -> None:
    """Tests call this to force a fresh engine per test session."""
    global _engine, _sessionmaker
    _engine = None
    _sessionmaker = None
