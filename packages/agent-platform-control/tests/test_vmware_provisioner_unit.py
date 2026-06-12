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
    with pytest.raises(ValueError, match="invalid vcenter_url"):
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


class _View:
    def __init__(self, vms):
        self.view = vms

    def Destroy(self):
        pass


class _Content:
    def __init__(self, vms):
        self.rootFolder = object()
        self._vms = vms

    class _vm_class:
        pass

    @property
    def viewManager(self):
        outer = self

        class _VM_Manager:
            def CreateContainerView(self, _root, _types, _recursive):
                return _View(outer._vms)

        return _VM_Manager()


def test_build_extra_config_stamps_platform_marker():
    opts = _build_extra_config("#cloud-config", "vm-1")
    assert any(o.key == PLATFORM_MANAGED_KEY and o.value == "1" for o in opts)


def test_find_vm_by_name_ignores_unmanaged_namesake():
    """An unrelated VM sharing the name must NOT be adopted (PR-review #103)."""
    content = _Content([_VM("vm-x", managed=False)])
    assert _find_vm_by_name(content, "vm-x") is None
    assert not _is_platform_managed(_VM("vm-x", managed=False))


def test_find_vm_by_name_adopts_managed_match():
    content = _Content([_VM("vm-x", managed=True)])
    found = _find_vm_by_name(content, "vm-x")
    assert found is not None and found.name == "vm-x"


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
