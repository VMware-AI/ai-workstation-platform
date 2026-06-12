"""Preflight: connectivity + existence checks before running #23+.

Exit 0 if all OK; 1 on any failure.

Usage: uv run python poc/nsx-dfw/scripts/preflight.py
"""
from __future__ import annotations

import os
import sys

from _common import NsxApiError, NsxClient


def main() -> int:
    failed = 0
    print("=== M0.8 NSX preflight ===")
    try:
        nsx = NsxClient()
    except ValueError as e:
        print(f"  FAIL config: {e}")
        return 1

    checks: list[tuple[str, str]] = [
        ("Edge cluster list", "/infra/sites/default/enforcement-points/default/edge-clusters"),
        ("Transport zones", "/infra/sites/default/enforcement-points/default/transport-zones"),
        ("T1 gateways", "/infra/tier-1s"),
        ("Default domain", "/infra/domains/default"),
    ]
    for name, path in checks:
        try:
            data = nsx.get(path)
            n = len(data.get("results", []))
            print(f"  OK  {name} ({n})")
        except NsxApiError as e:
            print(f"  FAIL {name}: {e}")
            failed += 1

    tz = os.environ.get("TRANSPORT_ZONE")
    if tz:
        path = f"/infra/sites/default/enforcement-points/default/transport-zones/{tz}"
        if nsx.exists(path):
            print(f"  OK  TZ '{tz}' exists")
        else:
            print(f"  FAIL TZ '{tz}' not found (check TRANSPORT_ZONE in .env)")
            failed += 1

    t1 = os.environ.get("T1_GATEWAY")
    if t1:
        if nsx.exists(f"/infra/tier-1s/{t1}"):
            print(f"  OK  T1 '{t1}' exists")
        else:
            print(f"  FAIL T1 '{t1}' not found")
            failed += 1

    print()
    if failed == 0:
        print("preflight passed ✓ — ready for #23 setup_segments.py")
        return 0
    print(f"{failed} check(s) failed; fix before continuing")
    return 1


if __name__ == "__main__":
    sys.exit(main())
