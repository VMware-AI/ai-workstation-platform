"""Parallel sync SQLAlchemy session for routers that aren't async.

The main control-plane runs on the async session (``db/session.py``).
C13 agent-platform-approval ships its router against a sync ``Session``, so
we maintain a second engine/sessionmaker bound to the same database
URL. FastAPI runs sync route handlers in a threadpool, so the two
sessions can coexist without a single hand-rolled async↔sync bridge.

Once C13 grows an async surface this module goes away.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import get_settings


def _to_sync_url(url: str) -> str:
    """Drop the async driver tag from a SQLAlchemy URL.

    ``postgresql+asyncpg://...`` → ``postgresql+psycopg://...``
    ``sqlite+aiosqlite:///...`` → ``sqlite:///...``

    The async driver libraries (asyncpg / aiosqlite) cannot serve a
    sync engine; we use the matching sync driver if one is conventional
    or fall through to the default.
    """
    if url.startswith("sqlite+aiosqlite:"):
        return "sqlite:" + url[len("sqlite+aiosqlite:") :]
    if url.startswith("postgresql+asyncpg:"):
        return "postgresql+psycopg:" + url[len("postgresql+asyncpg:") :]
    return url


_sync_engine = None
_sync_sessionmaker: sessionmaker[Session] | None = None


def get_sync_engine():
    global _sync_engine, _sync_sessionmaker
    if _sync_engine is None:
        url = _to_sync_url(get_settings().database_url)
        # check_same_thread=False is required for SQLite when sharing
        # the engine across the threadpool workers FastAPI uses for
        # sync endpoints. Pool defaults are fine for everything else.
        connect_args = {"check_same_thread": False} if url.startswith("sqlite:") else {}
        _sync_engine = create_engine(url, future=True, connect_args=connect_args)
        _sync_sessionmaker = sessionmaker(_sync_engine, expire_on_commit=False, class_=Session)
    return _sync_engine


def get_sync_session() -> Iterator[Session]:
    get_sync_engine()
    assert _sync_sessionmaker is not None  # nosec B101 — import-time invariant, not user input
    with _sync_sessionmaker() as session:
        yield session


def reset_for_tests() -> None:
    """Tests call this to force a fresh sync engine per test session."""
    global _sync_engine, _sync_sessionmaker
    _sync_engine = None
    _sync_sessionmaker = None
