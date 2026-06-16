"""Pytest fixtures for the M1.26 10-step acceptance demo.

Design notes:
- `agent_platform_env` reads endpoints from env vars; defaults are localhost stubs so
  the suite can be `--collect-only`'d on any laptop.
- Playwright fixtures import lazily so collection does not blow up on machines
  without browsers installed.
- `hw_blocked` marker auto-skips unless `AGENT_PLATFORM_HW_AVAILABLE=1`.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
import pytest_asyncio

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_platform_e2e.env import AgentPlatformEnv

if TYPE_CHECKING:
    from playwright.async_api import Browser, Page


# ---------------------------------------------------------------------------
# Hardware gating
# ---------------------------------------------------------------------------


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-skip `hw_blocked` tests unless AGENT_PLATFORM_HW_AVAILABLE=1.

    Lets `pytest -m "not hw_blocked"` work even before any hardware is wired.
    """
    env = AgentPlatformEnv.from_os_env()
    if env.hw_available:
        return
    skip_hw = pytest.mark.skip(
        reason="AGENT_PLATFORM_HW_AVAILABLE != 1 (no real GPU/NSX/vSAN in this env)"
    )
    for item in items:
        if "hw_blocked" in item.keywords:
            item.add_marker(skip_hw)


# ---------------------------------------------------------------------------
# Environment + HTTP client
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def agent_platform_env() -> AgentPlatformEnv:
    return AgentPlatformEnv.from_os_env()


# ---------------------------------------------------------------------------
# Deployment cleanup
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def created_deployments(agent_platform_env: AgentPlatformEnv) -> Iterator[list[str]]:
    """Session-level registry of deployment ids created against a live C1.

    Tests that POST /v1/deployments (step02) append the returned id here so the
    teardown can reclaim them — otherwise staging accretes orphan VMs on every
    run. Reclamation is via POST /{id}/cancel (C1 exposes no DELETE route);
    cancel marks pending items cancelled so the cleanup cron destroys their VMs.
    Cleanup is best-effort: a 404 (already gone) is tolerated, and any transport
    error is logged rather than failing the session, because a flaky teardown
    must not mask the test result.
    """
    ids: list[str] = []
    yield ids
    if not ids:
        return
    headers = {"Authorization": f"Bearer {agent_platform_env.admin_token}"}
    with httpx.Client(
        base_url=agent_platform_env.control_url, timeout=10.0, follow_redirects=True
    ) as client:
        for deployment_id in ids:
            try:
                resp = client.post(f"/v1/deployments/{deployment_id}/cancel", headers=headers)
                if resp.status_code not in (200, 202, 204, 404):
                    logging.warning(
                        "e2e cleanup: cancel deployment %s returned HTTP %s",
                        deployment_id,
                        resp.status_code,
                    )
            except httpx.HTTPError as exc:  # pragma: no cover — network-dependent
                logging.warning("e2e cleanup: cancel deployment %s failed: %s", deployment_id, exc)


@pytest_asyncio.fixture
async def control_client(agent_platform_env: AgentPlatformEnv) -> AsyncIterator[httpx.AsyncClient]:
    """HTTP client pointed at the C1 control-plane API."""
    async with httpx.AsyncClient(
        base_url=agent_platform_env.control_url, timeout=10.0, follow_redirects=True
    ) as c:
        yield c


@pytest.fixture
def admin_token(agent_platform_env: AgentPlatformEnv) -> str:
    """Placeholder admin bearer. Real Keycloak integration is task 1.4."""
    return agent_platform_env.admin_token


@pytest.fixture
def alice_token(agent_platform_env: AgentPlatformEnv) -> str:
    """Placeholder user (Alice) bearer. Real Keycloak integration is task 1.4."""
    return agent_platform_env.alice_token


@pytest.fixture
def admin_headers(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture
def alice_headers(alice_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {alice_token}"}


# ---------------------------------------------------------------------------
# Playwright (lazy — only imported if a test asks for it)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def playwright_browser() -> AsyncIterator[Browser]:
    try:
        from playwright.async_api import async_playwright
    except ImportError:  # pragma: no cover — covered by skip in tests
        pytest.skip("playwright not installed; run `playwright install chromium`")

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except Exception as e:  # pragma: no cover — browser missing on CI
            pytest.skip(f"chromium not available: {e}; run `playwright install chromium`")
        try:
            yield browser
        finally:
            await browser.close()


@pytest_asyncio.fixture
async def console_page(
    playwright_browser: Browser, agent_platform_env: AgentPlatformEnv
) -> AsyncIterator[Page]:
    """A fresh page already navigated to the C2 admin console root."""
    ctx = await playwright_browser.new_context()
    page = await ctx.new_page()
    try:
        await page.goto(agent_platform_env.console_url, wait_until="domcontentloaded")
    except Exception as e:
        await ctx.close()
        pytest.skip(f"console not reachable at {agent_platform_env.console_url}: {e}")
    try:
        yield page
    finally:
        await ctx.close()


@pytest_asyncio.fixture
async def portal_page(
    playwright_browser: Browser, agent_platform_env: AgentPlatformEnv
) -> AsyncIterator[Page]:
    """A fresh page already navigated to the C12 user portal root."""
    ctx = await playwright_browser.new_context()
    page = await ctx.new_page()
    try:
        await page.goto(agent_platform_env.portal_url, wait_until="domcontentloaded")
    except Exception as e:
        await ctx.close()
        pytest.skip(f"portal not reachable at {agent_platform_env.portal_url}: {e}")
    try:
        yield page
    finally:
        await ctx.close()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_demo_dir(tmp_path: Path) -> Iterator[Path]:
    """Scratch directory for demo artefacts (bundles, reports, etc.)."""
    d = tmp_path / "demo"
    d.mkdir()
    yield d
