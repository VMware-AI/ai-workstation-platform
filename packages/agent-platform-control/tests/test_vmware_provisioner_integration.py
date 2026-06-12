"""Integration tests against vcsim. Skipped without VCSIM_URL.

Bring vcsim up locally:
    docker compose -f docker-compose.vcsim.yml up -d
    export VCSIM_URL=https://localhost:8989 VCSIM_USER=user VCSIM_PASS=pass
    uv run pytest -m integration -v
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from agent_platform_control.orchestrator.protocol import CloneSpec
from agent_platform_control.orchestrator.vmware import VmwareProvisioner

pytestmark = pytest.mark.integration


CLOUD_INIT_TPL = (
    Path(__file__).resolve().parents[2]
    / "agent-platform-image"
    / "cloud-init"
    / "user-data.yaml.tpl"
)


@pytest.fixture
def vcsim_env():
    url = os.environ.get("VCSIM_URL")
    if not url:
        pytest.skip("VCSIM_URL not set; skipping integration tests")
    return {
        "url": url,
        "user": os.environ.get("VCSIM_USER", "user"),
        "password": os.environ.get("VCSIM_PASS", "pass"),
    }


@pytest.fixture
def provisioner(vcsim_env):
    if not CLOUD_INIT_TPL.exists():
        pytest.skip("C3 cloud-init template missing")
    return VmwareProvisioner(
        vcenter_url=vcsim_env["url"],
        vcenter_user=vcsim_env["user"],
        vcenter_password=vcsim_env["password"],
        cloud_init_template_path=CLOUD_INIT_TPL,
        verify_ssl=False,
        clone_timeout_s=60.0,
    )


def _spec(name: str, template: str = "DC0_H0_VM0") -> CloneSpec:
    return CloneSpec(
        intended_name=name,
        template=template,
        tenant_id="t-a",
        owner_id="alice",
        owner_login="alice",
        image_version="v0.1.0",
        registry_url="registry.test/agent-platform",
        goose_image_tag="1.34.1",
        litellm_gateway_url="http://gw:4000",
        user_token="tok_alice",
        heartbeat_url="http://ctl/v1/heartbeat",
    )


@pytest.mark.asyncio
async def test_clone_existing_vcsim_vm(provisioner):
    """vcsim ships DC0_H0_VM0 .. _VM3 pre-populated; clone one of them."""
    result = await provisioner.clone_vm(_spec("agent-platform-test-clone-1"))
    assert result.success, result.error
    assert result.vm_id is not None
    assert result.vm_id.startswith("vm-")


@pytest.mark.asyncio
async def test_clone_unknown_template_returns_failure(provisioner):
    result = await provisioner.clone_vm(
        _spec("agent-platform-test-fail", template="nonexistent-vm-xyz")
    )
    assert result.success is False
    assert result.error is not None
    assert "not found" in result.error.lower()
