"""Playwright video recording stub for the 10-step demo (task 1.26.2).

TODO: drive the actual demo flow via Playwright with `record_video_dir=`
to produce a ≤ 30-minute walkthrough .webm for sales / acceptance.

For now this is a placeholder so the entry point exists; running it just
prints what it WOULD do, exit code 0.
"""

from __future__ import annotations

import logging
import sys

from .env import AgentPlatformEnv

logger = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    env = AgentPlatformEnv.from_os_env()
    logger.info("would record demo against control=%s portal=%s", env.control_url, env.portal_url)
    logger.info("steps planned: 10 (see tests/test_demo_step01_to_step10.py)")
    logger.warning("recording NOT IMPLEMENTED — stub only (1.26.2)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
