"""#23 (0.8.2): 创建 2 个 tenant 的 NSX Segment（幂等）。

Usage: uv run python poc/nsx-dfw/scripts/setup_segments.py
       uv run python poc/nsx-dfw/scripts/setup_segments.py --delete
"""
from __future__ import annotations

import argparse
import os
import sys

from _common import NsxClient


def segments() -> list[dict]:
    return [
        {
            "id": "seg-tenant-a",
            "subnet": os.environ.get("TENANT_A_CIDR", "10.50.10.1/24"),
        },
        {
            "id": "seg-tenant-b",
            "subnet": os.environ.get("TENANT_B_CIDR", "10.50.20.1/24"),
        },
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delete", action="store_true", help="清理 (delete 2 segments)")
    args = ap.parse_args()

    nsx = NsxClient()
    tz = os.environ["TRANSPORT_ZONE"]
    t1 = os.environ["T1_GATEWAY"]

    for s in segments():
        path = f"/infra/segments/{s['id']}"
        if args.delete:
            nsx.delete(path)
            print(f"deleted: {s['id']}")
            continue
        body = {
            "display_name": s["id"],
            "subnets": [{"gateway_address": s["subnet"]}],
            "transport_zone_path": f"/infra/sites/default/enforcement-points/default/transport-zones/{tz}",
            "connectivity_path": f"/infra/tier-1s/{t1}",
        }
        nsx.patch(path, body)
        print(f"ok: {s['id']} ({s['subnet']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
