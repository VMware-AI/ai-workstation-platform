"""/version — return package version + git commit (best-effort)."""

from __future__ import annotations

import os
import subprocess  # nosec B404 — git describe only
from functools import lru_cache

from fastapi import APIRouter

from .. import __version__

router = APIRouter(tags=["meta"])


@lru_cache(maxsize=1)
def _git_commit() -> str:
    # 1) env override (CI / image build can inject)
    env_sha = os.environ.get("AGENT_PLATFORM_GIT_SHA")
    if env_sha:
        return env_sha
    # 2) fall back to runtime git probe (dev env)
    try:
        sha = (
            subprocess.check_output(  # nosec B603 B607 — constant argv; git from PATH
                ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607  # git from PATH; args are constants
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
            .decode()
            .strip()
        )
        return sha or "unknown"
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return "unknown"


@router.get("/version")
async def version() -> dict[str, str]:
    return {
        "package": "agent-platform-control",
        "version": __version__,
        "git_sha": _git_commit(),
    }
