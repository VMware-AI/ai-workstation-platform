"""Entry-point shim for `agent-platform-e2e-record` (task 1.26.2).

Real video recording lives in `agent_platform_e2e.record:main`. This file exists so
ops can `python scripts/record_demo.py` from the package root without having
to know the import path.
"""

from __future__ import annotations

import sys

from agent_platform_e2e.record import main

if __name__ == "__main__":
    sys.exit(main())
