"""#31 (0.8.10): 幂等的 per-tenant provisioner — C14 雏形。

把 #23 (segment) + #25 (groups) + #26 (DFW policy) 三件事合成一个命令，
重复调用安全；deprovision 反向清理无孤儿。

Usage:
  uv run python poc/nsx-dfw/scripts/provision_tenant.py \\
    --name c --pc-cidr 10.20.3.0/24 --fs-ip 10.30.3.5 --llm-ip 10.30.3.10

  uv run python poc/nsx-dfw/scripts/provision_tenant.py \\
    --name c --action deprovision

  uv run python poc/nsx-dfw/scripts/provision_tenant.py --list
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from _common import NsxClient

try:
    from jinja2 import Environment, FileSystemLoader
except ImportError:
    print("jinja2 required: uv pip install jinja2", file=sys.stderr)
    sys.exit(2)


TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


@dataclass(frozen=True)
class TenantSpec:
    name: str            # short lowercase, e.g. "a", "b", "phoenix-eng"
    cidr: str            # tenant VM subnet, e.g. "10.50.10.1/24"
    pc_cidr: str         # employee PC subnet, e.g. "10.20.1.0/24"
    fs_ip: str           # fileshare IP / CIDR, e.g. "10.30.1.5"
    llm_ip: str          # LLM gateway IP, e.g. "10.30.1.10"


# === segment ===

def _segment(t: TenantSpec) -> dict:
    return {
        "id": f"seg-tenant-{t.name}",
        "subnet": t.cidr,
    }


def apply_segment(nsx: NsxClient, t: TenantSpec, tz: str, t1: str) -> None:
    s = _segment(t)
    body = {
        "display_name": s["id"],
        "subnets": [{"gateway_address": s["subnet"]}],
        "transport_zone_path": f"/infra/sites/default/enforcement-points/default/transport-zones/{tz}",
        "connectivity_path": f"/infra/tier-1s/{t1}",
    }
    nsx.patch(f"/infra/segments/{s['id']}", body)


# === groups (4 per tenant) ===

def _group_specs(t: TenantSpec) -> list[dict]:
    vms = {
        "id": f"grp-tenant-{t.name}-vms",
        "expression": [
            {"resource_type": "Condition", "member_type": "VirtualMachine",
             "key": "Tag", "operator": "EQUALS", "value": f"tenant|{t.name}"},
            {"resource_type": "ConjunctionOperator", "conjunction_operator": "AND"},
            {"resource_type": "Condition", "member_type": "VirtualMachine",
             "key": "Tag", "operator": "EQUALS", "value": "agent-platform-vm"},
        ],
    }

    def _ip(name: str, ips: str) -> dict:
        return {"id": name, "expression": [{"resource_type": "IPAddressExpression",
                                            "ip_addresses": [s.strip() for s in ips.split(",") if s.strip()]}]}

    return [
        vms,
        _ip(f"grp-tenant-{t.name}-pcs", t.pc_cidr),
        _ip(f"grp-tenant-{t.name}-fs",  t.fs_ip),
        _ip(f"grp-tenant-{t.name}-llm", t.llm_ip),
    ]


def apply_groups(nsx: NsxClient, t: TenantSpec) -> None:
    for g in _group_specs(t):
        body = {"display_name": g["id"], "expression": g["expression"]}
        nsx.patch(f"/infra/domains/default/groups/{g['id']}", body)


# === DFW policy ===

def _render_policy(t: TenantSpec, other_tenants: list[str], sequence_number: int) -> dict:
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), keep_trailing_newline=True)
    rendered = env.get_template("per-tenant-policy.json.j2").render(
        tenant=t.name, other_tenants=other_tenants, sequence_number=sequence_number,
    )
    return json.loads(rendered)


def apply_dfw(nsx: NsxClient, t: TenantSpec, other_tenants: list[str], sequence_number: int) -> None:
    body = _render_policy(t, other_tenants, sequence_number)
    nsx.patch(f"/infra/domains/default/security-policies/agent-platform-tenant-{t.name}", body)


# === main entry points ===

def provision(nsx: NsxClient, t: TenantSpec, tz: str, t1: str, other_tenants: list[str], sequence_number: int) -> None:
    print(f"[1/3] segment seg-tenant-{t.name}")
    apply_segment(nsx, t, tz, t1)
    print(f"[2/3] groups (4) for tenant {t.name}")
    apply_groups(nsx, t)
    print(f"[3/3] DFW policy agent-platform-tenant-{t.name} (deny vs {other_tenants})")
    apply_dfw(nsx, t, other_tenants, sequence_number)
    print(f"OK provisioned tenant {t.name}")


def deprovision(nsx: NsxClient, name: str) -> None:
    """反向删除 — policy → groups → segment（顺序很关键，policy 引用 groups，groups 不能先删）。"""
    print(f"[1/3] delete DFW policy agent-platform-tenant-{name}")
    nsx.delete(f"/infra/domains/default/security-policies/agent-platform-tenant-{name}")
    print(f"[2/3] delete 4 groups for tenant {name}")
    for suffix in ("vms", "pcs", "fs", "llm"):
        nsx.delete(f"/infra/domains/default/groups/grp-tenant-{name}-{suffix}")
    print(f"[3/3] delete segment seg-tenant-{name}")
    nsx.delete(f"/infra/segments/seg-tenant-{name}")
    print(f"OK deprovisioned tenant {name}")


def list_tenants(nsx: NsxClient) -> list[str]:
    data = nsx.get("/infra/segments")
    out = []
    for seg in data.get("results", []):
        name = seg.get("id", "")
        if name.startswith("seg-tenant-"):
            out.append(name[len("seg-tenant-"):])
    return sorted(out)


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Idempotent per-tenant NSX provisioner (M0.8.10)")
    ap.add_argument("--action", choices=["provision", "deprovision"], default="provision")
    ap.add_argument("--name", help="tenant short name (required for provision/deprovision)")
    ap.add_argument("--cidr", help="tenant VM subnet (e.g. 10.50.10.1/24)")
    ap.add_argument("--pc-cidr", help="employee PC subnet")
    ap.add_argument("--fs-ip", help="fileshare IP")
    ap.add_argument("--llm-ip", help="LLM gateway IP")
    ap.add_argument("--other-tenants", default="", help="comma-separated names to deny cross-tenant (override auto-detect)")
    ap.add_argument("--sequence-number", type=int, default=100)
    ap.add_argument("--list", action="store_true", help="list existing tenants")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    nsx = NsxClient()

    if args.list:
        names = list_tenants(nsx)
        if not names:
            print("(none)")
        else:
            for n in names:
                print(n)
        return 0

    if not args.name:
        print("--name required for provision/deprovision", file=sys.stderr)
        return 2

    if args.action == "deprovision":
        deprovision(nsx, args.name)
        return 0

    # provision needs cidr/pc-cidr/fs-ip/llm-ip
    missing = [f for f, v in [("--cidr", args.cidr), ("--pc-cidr", args.pc_cidr),
                              ("--fs-ip", args.fs_ip), ("--llm-ip", args.llm_ip)] if not v]
    if missing:
        print(f"missing args for provision: {missing}", file=sys.stderr)
        return 2

    tenant = TenantSpec(name=args.name, cidr=args.cidr, pc_cidr=args.pc_cidr,
                       fs_ip=args.fs_ip, llm_ip=args.llm_ip)
    # auto-detect other_tenants
    if args.other_tenants:
        others = [s.strip() for s in args.other_tenants.split(",") if s.strip()]
    else:
        existing = list_tenants(nsx)
        others = [n for n in existing if n != args.name]

    tz = os.environ.get("TRANSPORT_ZONE")
    t1 = os.environ.get("T1_GATEWAY")
    if not tz or not t1:
        print("TRANSPORT_ZONE / T1_GATEWAY env required", file=sys.stderr)
        return 2

    provision(nsx, tenant, tz, t1, others, args.sequence_number)
    return 0


if __name__ == "__main__":
    sys.exit(main())
