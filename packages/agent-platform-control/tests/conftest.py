"""Test fixtures — fresh SQLite DB per test, HTTP client wired to fresh app."""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import agent_platform_control.db.session as session_mod
import pytest
import pytest_asyncio
from agent_platform_control import config
from agent_platform_control.app import create_app
from agent_platform_control.db.models import Base
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture(autouse=True)
def clean_settings_cache():
    config.get_settings.cache_clear()
    yield


@pytest.fixture(autouse=True)
def enable_fake_auth_for_tests(monkeypatch):
    # Production default is enable_fake_auth=False (secure by default, SEC-2).
    # The suite exercises both the X-User fake-auth path and the committed
    # default admin token, so the whole suite opts into dev/test auth mode
    # explicitly — mirroring how local dev sets it in .env.
    monkeypatch.setenv("AGENT_PLATFORM_ENABLE_FAKE_AUTH", "1")
    config.get_settings.cache_clear()
    yield


@pytest.fixture(autouse=True)
def reset_vm_secret_store():
    """Each test starts with a fresh InMemoryVmSecretStore (PR-C)."""
    from agent_platform_control.api.cloud_init import reset_vm_secret_store_for_tests

    reset_vm_secret_store_for_tests()
    yield
    reset_vm_secret_store_for_tests()


@pytest_asyncio.fixture
async def db_url(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    monkeypatch.setenv("AGENT_PLATFORM_DATABASE_URL", url)
    config.get_settings.cache_clear()
    yield url


@pytest_asyncio.fixture
async def engine(db_url):
    eng = create_async_engine(db_url)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def client(engine, monkeypatch):
    # Force db.session to use our test engine
    sm = async_sessionmaker(engine, expire_on_commit=False)
    session_mod._engine = engine
    session_mod._sessionmaker = sm
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    session_mod.reset_for_tests()
