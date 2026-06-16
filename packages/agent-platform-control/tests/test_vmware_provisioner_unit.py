"""Unit tests for VmwareProvisioner — pure-logic pieces (no vcsim required).

Integration tests live in test_vmware_provisioner_integration.py and are
gated on VCSIM_URL env.
"""
# ruff: noqa: N801, N802, N815 — test fakes mirror pyVmomi API casing

# test fakes mirror pyVmomi API casing

from __future__ import annotations

import base64
import dataclasses
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_platform_control.orchestrator.protocol import CloneSpec
from agent_platform_control.orchestrator.vmware import (
    PLATFORM_MANAGED_KEY,
    VmwareProvisioner,
    _build_extra_config,
    _find_vm_by_name,
    _is_platform_managed,
    _render_userdata,
    _validate_ttyd_allow_cidr,
)
from pyVmomi import vim, vmodl

CLOUD_INIT_TPL = (
    Path(__file__).resolve().parents[2]
    / "agent-platform-image"
    / "cloud-init"
    / "user-data.yaml.tpl"
)


def _sample_spec() -> CloneSpec:
    return CloneSpec(
        intended_name="vm-alice-001",
        template="DC0_C0_RP0_VM0",  # vcsim flat name
        tenant_id="t-a",
        owner_id="alice",
        owner_login="alice",
        image_version="v0.1.0",
        registry_url="registry.test/agent-platform",
        goose_image_tag="1.34.1",
        litellm_gateway_url="http://gw:4000",
        user_token="tok_abc",
        heartbeat_url="http://ctl/v1/heartbeat",
    )


@pytest.mark.skipif(not CLOUD_INIT_TPL.exists(), reason="C3 template not in workspace")
def test_render_userdata_substitutes_all_keys():
    rendered = _render_userdata(_sample_spec(), CLOUD_INIT_TPL)
    assert "registry.test/agent-platform" in rendered
    assert "1.34.1" in rendered
    assert "tok_abc" in rendered
    assert "alice" in rendered
    assert "{{ " not in rendered
    assert " }}" not in rendered


@pytest.mark.skipif(not CLOUD_INIT_TPL.exists(), reason="C3 template not in workspace")
def test_render_userdata_ttyd_cidr_fail_closed_by_default():
    """SEC-1: a spec without a trusted CIDR renders TTYD_ALLOW_CIDR empty, so
    install-agent.sh leaves ttyd:7681 firewalled (no unauthenticated shell)."""
    rendered = _render_userdata(_sample_spec(), CLOUD_INIT_TPL)
    assert "TTYD_ALLOW_CIDR=\n" in rendered
    assert "{{ " not in rendered


@pytest.mark.skipif(not CLOUD_INIT_TPL.exists(), reason="C3 template not in workspace")
def test_render_userdata_ttyd_cidr_supplied_opens_segment():
    """A spec carrying a CIDR threads it into install.env verbatim (normalised)."""
    spec = dataclasses.replace(_sample_spec(), ttyd_allow_cidr="10.20.0.0/16")
    rendered = _render_userdata(spec, CLOUD_INIT_TPL)
    assert "TTYD_ALLOW_CIDR=10.20.0.0/16" in rendered


@pytest.mark.skipif(not CLOUD_INIT_TPL.exists(), reason="C3 template not in workspace")
def test_render_userdata_rejects_malformed_ttyd_cidr():
    """A malformed CIDR fails the clone rather than corrupting install.env / ufw."""
    spec = dataclasses.replace(_sample_spec(), ttyd_allow_cidr="10.0.0.0/16; rm -rf /")
    with pytest.raises(ValueError, match="TTYD_ALLOW_CIDR"):
        _render_userdata(spec, CLOUD_INIT_TPL)


@pytest.mark.skipif(not CLOUD_INIT_TPL.exists(), reason="C3 template not in workspace")
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("user_token", "tok_abc\nruncmd:\n  - rm -rf /"),  # newline → YAML injection
        ("agent_user", "alice\revil"),  # carriage return
        ("agent_version", "1.0\x1b[31m"),  # ESC control char
    ],
)
def test_render_userdata_rejects_control_chars_in_values(field: str, value: str):
    """Fail-closed guard: any interpolated value carrying a newline or control
    character must abort the render — a stray newline inside guestinfo userdata
    would inject arbitrary cloud-init YAML into the guest."""
    spec = dataclasses.replace(_sample_spec(), **{field: value})
    with pytest.raises(ValueError, match="control character"):
        _render_userdata(spec, CLOUD_INIT_TPL)


def test_validate_ttyd_allow_cidr_accepts_ip_cidr_and_empty():
    assert _validate_ttyd_allow_cidr("") == ""
    assert _validate_ttyd_allow_cidr("  ") == ""
    assert _validate_ttyd_allow_cidr("10.20.0.0/16") == "10.20.0.0/16"
    # bare host address normalises to a /32
    assert _validate_ttyd_allow_cidr("10.0.0.5") == "10.0.0.5/32"
    # IPv6 supported too
    assert _validate_ttyd_allow_cidr("fd00::/8") == "fd00::/8"


@pytest.mark.parametrize(
    "bad",
    ["not-a-cidr", "10.0.0.0/33", "10.0.0.0/16\nFOO=bar", "$(reboot)", "10.0.0.0 16"],
)
def test_validate_ttyd_allow_cidr_rejects_garbage(bad: str):
    with pytest.raises(ValueError, match="TTYD_ALLOW_CIDR"):
        _validate_ttyd_allow_cidr(bad)


@pytest.mark.parametrize("default_route", ["0.0.0.0/0", "::/0"])
def test_validate_ttyd_allow_cidr_rejects_default_route(default_route: str):
    """SEC-1 hardening (建议2): a /0 opens the in-VM ttyd:7681 shell to the
    entire network with no auth — the exact thing fail-closed guards against,
    and almost always a misconfig. Reject the default route explicitly."""
    with pytest.raises(ValueError, match="TTYD_ALLOW_CIDR"):
        _validate_ttyd_allow_cidr(default_route)


@pytest.mark.parametrize(
    "zoned",
    ["fe80::1%eth0", "fe80::1%$(id)", 'fe80::1%"; ufw allow', "fe80::1% rm -rf /"],
)
def test_validate_ttyd_allow_cidr_rejects_ipv6_zone_id(zoned: str):
    """Review HIGH: ip_network(strict=False) preserves an IPv6 zone id ('%…')
    verbatim through str(), so a value like `fe80::1%"; x` would slip past
    validation and corrupt install.env / the ufw rule (the source line bash
    sources). A scoped/link-local source is never valid for `ufw allow from`
    anyway, so reject any '%' outright."""
    with pytest.raises(ValueError, match="TTYD_ALLOW_CIDR"):
        _validate_ttyd_allow_cidr(zoned)


def test_build_extra_config_emits_guestinfo_pairs():
    pairs = _build_extra_config("hello world", "vm-001")
    keys = {p.key for p in pairs}
    assert keys == {
        "guestinfo.userdata",
        "guestinfo.userdata.encoding",
        "guestinfo.metadata",
        "guestinfo.metadata.encoding",
        PLATFORM_MANAGED_KEY,
    }
    userdata = next(p for p in pairs if p.key == "guestinfo.userdata")
    assert base64.b64decode(userdata.value).decode() == "hello world"

    encoding = next(p for p in pairs if p.key == "guestinfo.userdata.encoding")
    assert encoding.value == "base64"


def test_init_rejects_bad_url(tmp_path):
    tpl = tmp_path / "u.yaml"
    tpl.write_text("#cloud-config")
    with pytest.raises(ValueError, match="invalid vcenter url"):
        VmwareProvisioner(
            vcenter_url="://not-a-url",
            vcenter_user="u",
            vcenter_password="p",
            cloud_init_template_path=tpl,
        )


def test_init_rejects_missing_template(tmp_path):
    with pytest.raises(FileNotFoundError):
        VmwareProvisioner(
            vcenter_url="https://vc.example/sdk",
            vcenter_user="u",
            vcenter_password="p",
            cloud_init_template_path=tmp_path / "does-not-exist.yaml",
        )


class _Opt:
    def __init__(self, key, value):
        self.key = key
        self.value = value


class _VM:
    def __init__(self, name, *, managed):
        self.name = name

        class _cfg:
            extraConfig = [_Opt(PLATFORM_MANAGED_KEY, "1")] if managed else []

        self.config = _cfg


def _props_for(vms):
    """Mimic ``_retrieve_vm_props(content, ["name","config.extraConfig"])``:
    one (vm, props) tuple per VM, properties already materialized (the whole
    point of the PropertyCollector path — no per-VM lazy fetch)."""
    return [(vm, {"name": vm.name, "config.extraConfig": vm.config.extraConfig}) for vm in vms]


def test_build_extra_config_stamps_platform_marker():
    opts = _build_extra_config("#cloud-config", "vm-1")
    assert any(o.key == PLATFORM_MANAGED_KEY and o.value == "1" for o in opts)


def test_find_vm_by_name_ignores_unmanaged_namesake(monkeypatch):
    """An unrelated VM sharing the name must NOT be adopted (PR-review #103)."""
    from agent_platform_control.orchestrator import vmware as vmware_mod

    vms = [_VM("vm-x", managed=False)]
    monkeypatch.setattr(vmware_mod, "_retrieve_vm_props", lambda _c, _p: _props_for(vms))
    assert _find_vm_by_name(object(), "vm-x") is None
    assert not _is_platform_managed(_VM("vm-x", managed=False))


def test_find_vm_by_name_adopts_managed_match(monkeypatch):
    from agent_platform_control.orchestrator import vmware as vmware_mod

    vms = [_VM("vm-x", managed=True)]
    monkeypatch.setattr(vmware_mod, "_retrieve_vm_props", lambda _c, _p: _props_for(vms))
    found = _find_vm_by_name(object(), "vm-x")
    assert found is not None and found.name == "vm-x"


def test_find_vm_by_name_single_bulk_fetch_not_per_vm(monkeypatch):
    """Idempotency check must issue exactly one bulk property fetch regardless
    of inventory size — guards the O(N) per-VM RPC regression (#353 AC3)."""
    from agent_platform_control.orchestrator import vmware as vmware_mod

    vms = [_VM(f"vm-{i}", managed=False) for i in range(50)] + [_VM("target", managed=True)]
    calls = {"n": 0}

    def _spy(_content, _paths):
        calls["n"] += 1
        return _props_for(vms)

    monkeypatch.setattr(vmware_mod, "_retrieve_vm_props", _spy)
    found = _find_vm_by_name(object(), "target")
    assert found is not None and found.name == "target"
    assert calls["n"] == 1, f"expected 1 bulk fetch, got {calls['n']}"


# ---------------------------------------------------------- destroy: moRef-direct


class _FakeStub:
    pass


class _FakeSIWithStub:
    _stub = _FakeStub()


class _FakeTarget:
    """Stands in for the VirtualMachine ref _vm_ref builds from a MoRef id."""

    def __init__(
        self,
        *,
        power_state,
        missing=False,
        vanish_on_destroy=False,
        vanish_on_poweroff=False,
    ):
        self._power_state = power_state
        self._missing = missing
        self._vanish_on_destroy = vanish_on_destroy
        self._vanish_on_poweroff = vanish_on_poweroff
        self.powered_off = False
        self.destroyed = False

    @property
    def runtime(self):
        if self._missing:
            raise vmodl.fault.ManagedObjectNotFound()
        return SimpleNamespace(powerState=self._power_state)

    def PowerOffVM_Task(self):
        if self._vanish_on_poweroff:
            raise vmodl.fault.ManagedObjectNotFound()
        self.powered_off = True
        return "power-off-task"

    def Destroy_Task(self):
        if self._vanish_on_destroy:
            raise vmodl.fault.ManagedObjectNotFound()
        self.destroyed = True
        return "destroy-task"


def _wire_destroy(monkeypatch, target):
    """Point _destroy_sync at a fake SI + fake target, no-op the task waiter."""
    from agent_platform_control.orchestrator import vmware as vmware_mod

    monkeypatch.setattr(vmware_mod, "_connect", lambda _t: _FakeSIWithStub())
    monkeypatch.setattr(vmware_mod, "_vm_ref", lambda _si, _vm_id: target)
    monkeypatch.setattr(vmware_mod, "_wait_sync", lambda task, timeout_s=None: None)
    return vmware_mod


def _provisioner(tmp_path):
    tpl = tmp_path / "u.yaml"
    tpl.write_text("#cloud-config")
    return VmwareProvisioner(
        vcenter_url="https://vc.example/sdk",
        vcenter_user="u",
        vcenter_password="p",
        cloud_init_template_path=tpl,
    )


def test_destroy_sync_powers_off_running_then_destroys(tmp_path, monkeypatch):
    target = _FakeTarget(power_state=vim.VirtualMachinePowerState.poweredOn)
    _wire_destroy(monkeypatch, target)
    _provisioner(tmp_path)._destroy_sync("vm-123")
    assert target.powered_off is True
    assert target.destroyed is True


def test_destroy_sync_skips_power_off_when_already_off(tmp_path, monkeypatch):
    target = _FakeTarget(power_state=vim.VirtualMachinePowerState.poweredOff)
    _wire_destroy(monkeypatch, target)
    _provisioner(tmp_path)._destroy_sync("vm-123")
    assert target.powered_off is False
    assert target.destroyed is True


def test_destroy_sync_missing_vm_is_idempotent_success(tmp_path, monkeypatch):
    """A VM already gone raises ManagedObjectNotFound on first access — treated
    as success, no destroy attempted (post-condition already met)."""
    target = _FakeTarget(power_state=vim.VirtualMachinePowerState.poweredOff, missing=True)
    _wire_destroy(monkeypatch, target)
    _provisioner(tmp_path)._destroy_sync("vm-gone")  # must not raise
    assert target.destroyed is False


def test_destroy_sync_vanish_during_destroy_is_success(tmp_path, monkeypatch):
    """Racing reaper deletes the VM between power-off and destroy — still OK."""
    target = _FakeTarget(power_state=vim.VirtualMachinePowerState.poweredOn, vanish_on_destroy=True)
    _wire_destroy(monkeypatch, target)
    _provisioner(tmp_path)._destroy_sync("vm-racing")  # must not raise
    assert target.powered_off is True
    assert target.destroyed is False


def test_destroy_sync_vanish_during_power_off_is_success(tmp_path, monkeypatch):
    """Racing reaper deletes the VM between the powerState read and power-off —
    still success (post-condition 'absent from inventory' already met)."""
    target = _FakeTarget(
        power_state=vim.VirtualMachinePowerState.poweredOn, vanish_on_poweroff=True
    )
    _wire_destroy(monkeypatch, target)
    _provisioner(tmp_path)._destroy_sync("vm-racing-poweroff")  # must not raise
    assert target.powered_off is False
    assert target.destroyed is False


@pytest.mark.asyncio
async def test_clone_vm_idempotent_when_vm_exists(tmp_path, monkeypatch):
    """Protocol.clone_vm contract: an existing VM with the intended name must
    be returned as a successful clone (no DuplicateName error). Guards #81
    worker double-claim race.
    """
    tpl = tmp_path / "u.yaml"
    tpl.write_text("#cloud-config")

    class _FakeVM:
        _moId = "vm-existing-123"
        name = "vm-already-there"

        class guest:
            ipAddress = "10.0.0.42"

    class _FakeContent:
        rootFolder = object()
        viewManager = None  # not consulted; we monkeypatch _find_vm_by_name

    class _FakeSI:
        def RetrieveContent(self):
            return _FakeContent()

    from agent_platform_control.orchestrator import vmware as vmware_mod

    monkeypatch.setattr(vmware_mod, "_connect", lambda _target: _FakeSI())
    monkeypatch.setattr(
        vmware_mod,
        "_find_vm_by_name",
        lambda _content, name: _FakeVM() if name == "vm-already-there" else None,
    )

    prov = VmwareProvisioner(
        vcenter_url="https://vc.example/sdk",
        vcenter_user="u",
        vcenter_password="p",
        cloud_init_template_path=tpl,
    )
    result = await prov.clone_vm(
        CloneSpec(
            intended_name="vm-already-there",
            template="any",
            tenant_id="t",
            owner_id="u",
            owner_login="u",
            image_version="v",
            registry_url="r",
            goose_image_tag="g",
            litellm_gateway_url="l",
            user_token="t",
            heartbeat_url="h",
        )
    )
    assert result.success is True
    assert result.vm_id == "vm-existing-123"
    assert result.ip_address == "10.0.0.42"
