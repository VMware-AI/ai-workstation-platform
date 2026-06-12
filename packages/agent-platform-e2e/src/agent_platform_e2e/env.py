"""Environment configuration for the e2e suite.

Reads endpoints + credentials from env vars so the same suite runs against:
- local stubs (default)
- a staging cluster (set AGENT_PLATFORM_*_URL)
- a real customer site (set everything + AGENT_PLATFORM_HW_AVAILABLE=1)
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentPlatformEnv:
    """Immutable snapshot of the environment under test."""

    control_url: str
    console_url: str
    portal_url: str
    fileshare_unc: str
    admin_token: str
    alice_token: str
    hw_available: bool

    @classmethod
    def from_os_env(cls) -> AgentPlatformEnv:
        return cls(
            control_url=os.environ.get("AGENT_PLATFORM_CONTROL_URL", "http://localhost:8000"),
            console_url=os.environ.get("AGENT_PLATFORM_CONSOLE_URL", "http://localhost:5173"),
            portal_url=os.environ.get("AGENT_PLATFORM_PORTAL_URL", "http://localhost:5174"),
            fileshare_unc=os.environ.get(
                "AGENT_PLATFORM_FILESHARE_UNC", r"\\fs.agent-platform.local\u\alice"
            ),
            admin_token=os.environ.get("AGENT_PLATFORM_ADMIN_TOKEN", "dev-admin-token-CHANGE-ME"),
            alice_token=os.environ.get("AGENT_PLATFORM_ALICE_TOKEN", "dev-alice-token"),
            hw_available=os.environ.get("AGENT_PLATFORM_HW_AVAILABLE", "0") == "1",
        )
