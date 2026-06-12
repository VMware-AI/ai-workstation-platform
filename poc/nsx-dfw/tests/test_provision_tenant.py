"""Unit tests for provision_tenant.py — mocks NSX, no real API."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import provision_tenant as pt  # noqa: E402


@pytest.fixture
def spec() -> pt.TenantSpec:
    return pt.TenantSpec(
        name="c",
        cidr="10.50.30.1/24",
        pc_cidr="10.20.3.0/24",
        fs_ip="10.30.3.5",
        llm_ip="10.30.3.10",
    )


@pytest.fixture
def fake_nsx() -> MagicMock:
    n = MagicMock()
    n.patch.return_value = {}
    n.get.return_value = {"results": []}
    n.delete.return_value = None
    return n


def test_segment_payload_shape(fake_nsx, spec):
    pt.apply_segment(fake_nsx, spec, tz="tz-1", t1="t1-1")
    path, body = fake_nsx.patch.call_args[0][0], fake_nsx.patch.call_args[0][1]
    assert path == "/infra/segments/seg-tenant-c"
    assert body["display_name"] == "seg-tenant-c"
    assert body["subnets"][0]["gateway_address"] == "10.50.30.1/24"
    assert "tz-1" in body["transport_zone_path"]
    assert body["connectivity_path"].endswith("/t1-1")


def test_groups_create_4(fake_nsx, spec):
    pt.apply_groups(fake_nsx, spec)
    assert fake_nsx.patch.call_count == 4
    paths = [c[0][0] for c in fake_nsx.patch.call_args_list]
    assert "/infra/domains/default/groups/grp-tenant-c-vms" in paths
    assert "/infra/domains/default/groups/grp-tenant-c-pcs" in paths
    assert "/infra/domains/default/groups/grp-tenant-c-fs" in paths
    assert "/infra/domains/default/groups/grp-tenant-c-llm" in paths


def test_vm_group_dynamic_by_tag(fake_nsx, spec):
    pt.apply_groups(fake_nsx, spec)
    vm_call = next(c for c in fake_nsx.patch.call_args_list
                   if c[0][0].endswith("-vms"))
    body = vm_call[0][1]
    exprs = body["expression"]
    assert any(e.get("key") == "Tag" and e.get("value") == "tenant|c" for e in exprs)
    assert any(e.get("key") == "Tag" and e.get("value") == "agent-platform-vm" for e in exprs)


def test_ip_group_with_single_ip(fake_nsx, spec):
    pt.apply_groups(fake_nsx, spec)
    fs_call = next(c for c in fake_nsx.patch.call_args_list if c[0][0].endswith("-fs"))
    body = fs_call[0][1]
    expr = body["expression"][0]
    assert expr["resource_type"] == "IPAddressExpression"
    assert expr["ip_addresses"] == ["10.30.3.5"]


def test_render_policy_has_6_rules(spec):
    policy = pt._render_policy(spec, other_tenants=["a", "b"], sequence_number=100)
    assert policy["display_name"] == "agent-platform-tenant-c"
    assert len(policy["rules"]) == 6
    rule_names = [r["display_name"] for r in policy["rules"]]
    assert "deny-cross-tenant" in rule_names
    assert "deny-vms-to-internet" in rule_names


def test_render_policy_includes_other_tenants(spec):
    policy = pt._render_policy(spec, other_tenants=["a", "b"], sequence_number=100)
    deny_xtenant = next(r for r in policy["rules"] if r["display_name"] == "deny-cross-tenant")
    dests = " ".join(deny_xtenant["destination_groups"])
    assert "grp-tenant-a-vms" in dests
    assert "grp-tenant-b-vms" in dests
    # 不能 deny 自己
    assert "grp-tenant-c-vms" not in dests


def test_deprovision_order_policy_then_groups_then_segment(fake_nsx):
    pt.deprovision(fake_nsx, "c")
    paths = [c[0][0] for c in fake_nsx.delete.call_args_list]
    # 第一删 policy, 然后 4 groups, 最后 segment
    assert paths[0] == "/infra/domains/default/security-policies/agent-platform-tenant-c"
    assert any("groups/grp-tenant-c" in p for p in paths[1:5])
    assert paths[-1] == "/infra/segments/seg-tenant-c"


def test_list_tenants_filters_prefix(fake_nsx):
    fake_nsx.get.return_value = {"results": [
        {"id": "seg-tenant-a"},
        {"id": "seg-tenant-b"},
        {"id": "seg-tenant-phoenix-eng"},
        {"id": "seg-some-other"},  # filtered out
    ]}
    names = pt.list_tenants(fake_nsx)
    assert names == ["a", "b", "phoenix-eng"]


def test_main_list_dispatch():
    with patch("provision_tenant.NsxClient") as Cli, \
         patch("provision_tenant.list_tenants", return_value=["a", "b"]):
        Cli.return_value = MagicMock()
        rc = pt.main(["--list"])
    assert rc == 0


def test_main_provision_missing_args_returns_2():
    with patch("provision_tenant.NsxClient"):
        rc = pt.main(["--name", "c"])
    assert rc == 2


def test_main_deprovision_no_name_returns_2():
    with patch("provision_tenant.NsxClient"):
        rc = pt.main(["--action", "deprovision"])
    assert rc == 2


def test_idempotency_repeated_provision_no_error(spec):
    """同样输入跑 3 次必须不抛 — apply_* 都用 PATCH (upsert)。"""
    fake = MagicMock()
    fake.patch.return_value = {}
    fake.get.return_value = {"results": []}
    for _ in range(3):
        pt.apply_segment(fake, spec, tz="tz-1", t1="t1-1")
        pt.apply_groups(fake, spec)
    # 3 segments × 1 + 3 × 4 groups = 15 PATCH 调用，无异常
    assert fake.patch.call_count == 15


def test_render_policy_valid_json(spec):
    """模板渲染必须是合法 JSON（防 j2 误吞引号）。"""
    policy = pt._render_policy(spec, other_tenants=["a"], sequence_number=200)
    serialized = json.dumps(policy)
    assert "agent-platform-tenant-c" in serialized
