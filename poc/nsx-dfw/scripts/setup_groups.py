"""#25 (0.8.4): 创建 per-tenant Security Groups (dynamic by tag + IP-based)。

为每个 tenant 创建 4 个 group:
  - grp-tenant-<X>-vms  (dynamic: VM tag tenant=<X> AND agent-platform-vm)
  - grp-tenant-<X>-pcs  (ip-based: 员工 PC 网段)
  - grp-tenant-<X>-fs   (ip-based: fileshare 节点)
  - grp-tenant-<X>-llm  (ip-based: LLM 网关)

Usage:
  uv run python poc/nsx-dfw/scripts/setup_groups.py
  uv run python poc/nsx-dfw/scripts/setup_groups.py --delete
"""
from __future__ import annotations

import argparse
import os
import sys

from _common import NsxClient


def vm_group(tenant: str) -> dict:
    return {
        "id": f"grp-tenant-{tenant}-vms",
        "expression": [
            {
                "resource_type": "Condition",
                "member_type": "VirtualMachine",
                "key": "Tag",
                "operator": "EQUALS",
                "value": f"tenant|{tenant}",
            },
            {"resource_type": "ConjunctionOperator", "conjunction_operator": "AND"},
            {
                "resource_type": "Condition",
                "member_type": "VirtualMachine",
                "key": "Tag",
                "operator": "EQUALS",
                "value": "agent-platform-vm",
            },
        ],
    }


def ip_group(name: str, ips: str) -> dict:
    addrs = [s.strip() for s in ips.split(",") if s.strip()]
    return {
        "id": name,
        "expression": [{"resource_type": "IPAddressExpression", "ip_addresses": addrs}],
    }


def all_groups(tenant: str) -> list[dict]:
    upper = tenant.upper()
    return [
        vm_group(tenant),
        ip_group(f"grp-tenant-{tenant}-pcs", os.environ[f"TENANT_{upper}_PCS"]),
        ip_group(f"grp-tenant-{tenant}-fs", os.environ[f"TENANT_{upper}_FS"]),
        ip_group(f"grp-tenant-{tenant}-llm", os.environ[f"TENANT_{upper}_LLM"]),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delete", action="store_true")
    args = ap.parse_args()

    nsx = NsxClient()
    for tenant in ("a", "b"):
        for g in all_groups(tenant):
            path = f"/infra/domains/default/groups/{g['id']}"
            if args.delete:
                nsx.delete(path)
                print(f"deleted: {g['id']}")
                continue
            body = {"display_name": g["id"], "expression": g["expression"]}
            nsx.patch(path, body)
            print(f"ok: {g['id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
