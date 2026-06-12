"""/api/cloud-init/exchange-token — per-VM secret bootstrap (Task 1.20.3).

Covers the wire contract cloud-init relies on: bearer auth via per-VM
token, single-use enforcement (replay → 410), and the stable payload
shape.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from agent_platform_control.db.models import (
    VM,
    Deployment,
    DeploymentItem,
    Tenant,
    User,
)
from agent_platform_control.orchestrator.tokens import (
    encrypt_user_token,
    generate_user_token,
    hash_user_token,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker


@pytest_asyncio.fixture
async def seeded(engine):
    """One deployment with two items: one bound to a VM, one not.

    Returns ``(plaintext_token_bound, plaintext_token_unbound)`` so each
    test can drive whichever scenario it needs.
    """
    sm = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)

    tok_bound = generate_user_token()
    tok_unbound = generate_user_token()

    async with sm() as s:
        s.add(Tenant(id="t-a", display_name="Tenant A"))
        s.add(User(id="alice", email="a@x", display_name="A", tenant_id="t-a"))
        s.add(User(id="bob", email="b@x", display_name="B", tenant_id="t-a"))
        s.add(
            VM(
                id="vm-1",
                name="alice-vm",
                tenant_id="t-a",
                owner_id="alice",
                image_version="v0.1.0",
                state="running",
                created_at=now,
            )
        )
        s.add(
            Deployment(
                id="dep-1",
                tenant_id="t-a",
                requested_by="alice",
                template="default",
                image_version="v0.1.0",
                state="succeeded",
                requested_count=2,
                succeeded_count=2,
                created_at=now,
                updated_at=now,
            )
        )
        s.add(
            DeploymentItem(
                id=101,
                deployment_id="dep-1",
                owner_id="alice",
                intended_name="alice-vm",
                vm_id="vm-1",
                state="powered_on",
                user_token_enc=encrypt_user_token(tok_bound),
                user_token_hash=hash_user_token(tok_bound),
            )
        )
        s.add(
            DeploymentItem(
                id=102,
                deployment_id="dep-1",
                owner_id="bob",
                intended_name="bob-vm",
                vm_id=None,
                state="customizing",
                user_token_enc=encrypt_user_token(tok_unbound),
                user_token_hash=hash_user_token(tok_unbound),
            )
        )
        await s.commit()
    return tok_bound, tok_unbound


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_exchange_returns_payload_for_bound_vm(client, seeded):
    """Decision 4 lock 2026-05-29: payload always has the 4 M1 secret keys,
    even when the store has nothing for this VM yet — values degrade to
    empty strings or None so cloud-init scripts don't crash on missing keys."""
    tok_bound, _ = seeded
    r = await client.post("/api/cloud-init/exchange-token", headers=_bearer(tok_bound))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {
        "vm_id": "vm-1",
        "deployment_item_id": 101,
        "owner_id": "alice",
        "secrets": {
            "llm_gateway_api_key": "",
            "registry_pull_creds": None,
            "ssh_authorized_keys": "",
            "heartbeat_access_token": "",
        },
    }


@pytest.mark.asyncio
async def test_exchange_returns_payload_when_vm_not_yet_bound(client, seeded):
    """Cloud-init may call before the worker has stamped vm_id; payload
    still returns with ``vm_id=None`` so the boot can proceed."""
    _, tok_unbound = seeded
    r = await client.post("/api/cloud-init/exchange-token", headers=_bearer(tok_unbound))
    assert r.status_code == 200
    body = r.json()
    assert body["vm_id"] is None
    assert body["deployment_item_id"] == 102
    assert body["owner_id"] == "bob"


# ----------------------------------------------------------- secrets payload (C-5)


@pytest.mark.asyncio
async def test_exchange_returns_populated_per_tenant_secrets(client, seeded):
    """Decision 4 PR-C: per-tenant secrets land in the per-VM payload."""
    from agent_platform_control.api.cloud_init import get_vm_secret_store
    from agent_platform_secrets.vm_secret_store import VmSecretRef

    store = get_vm_secret_store()
    await store.put(VmSecretRef("tenant", "t-a", "llm_gateway_api_key"), "sk-test-tenant-a")
    await store.put(
        VmSecretRef("tenant", "t-a", "registry_pull_creds"),
        '{"username":"pull-bot","password":"hunter2"}',
    )

    tok_bound, _ = seeded
    r = await client.post("/api/cloud-init/exchange-token", headers=_bearer(tok_bound))
    assert r.status_code == 200
    secrets = r.json()["secrets"]
    assert secrets["llm_gateway_api_key"] == "sk-test-tenant-a"
    assert secrets["registry_pull_creds"] == {
        "username": "pull-bot",
        "password": "hunter2",
    }


@pytest.mark.asyncio
async def test_exchange_returns_populated_per_vm_secrets(client, seeded):
    """Decision 4 PR-C: per-VM secrets (SSH + heartbeat) land in payload."""
    from agent_platform_control.api.cloud_init import get_vm_secret_store
    from agent_platform_secrets.vm_secret_store import VmSecretRef

    store = get_vm_secret_store()
    await store.put(
        VmSecretRef("vm", "101", "ssh_authorized_keys"),
        "ssh-ed25519 AAAA... alice@laptop",
    )
    await store.put(
        VmSecretRef("vm", "101", "heartbeat_access_token"),
        "hbt_abc123",
    )

    tok_bound, _ = seeded
    r = await client.post("/api/cloud-init/exchange-token", headers=_bearer(tok_bound))
    secrets = r.json()["secrets"]
    assert secrets["ssh_authorized_keys"] == "ssh-ed25519 AAAA... alice@laptop"
    assert secrets["heartbeat_access_token"] == "hbt_abc123"


@pytest.mark.asyncio
async def test_exchange_other_tenant_secrets_are_not_returned(client, seeded):
    """Decision 9 prefix enforcement: a tenant-b secret is invisible to a
    tenant-a item, even if the keys happen to have the same name."""
    from agent_platform_control.api.cloud_init import get_vm_secret_store
    from agent_platform_secrets.vm_secret_store import VmSecretRef

    store = get_vm_secret_store()
    await store.put(VmSecretRef("tenant", "t-b", "llm_gateway_api_key"), "sk-leaky")

    tok_bound, _ = seeded  # tok_bound is on a tenant-a item
    r = await client.post("/api/cloud-init/exchange-token", headers=_bearer(tok_bound))
    assert r.json()["secrets"]["llm_gateway_api_key"] == ""


@pytest.mark.asyncio
async def test_exchange_invalid_registry_creds_json_degrades_to_null(client, seeded):
    """Defensive: store corruption surfaces as None, not 500."""
    from agent_platform_control.api.cloud_init import get_vm_secret_store
    from agent_platform_secrets.vm_secret_store import VmSecretRef

    store = get_vm_secret_store()
    await store.put(
        VmSecretRef("tenant", "t-a", "registry_pull_creds"),
        "not valid json {[",
    )

    tok_bound, _ = seeded
    r = await client.post("/api/cloud-init/exchange-token", headers=_bearer(tok_bound))
    assert r.status_code == 200
    assert r.json()["secrets"]["registry_pull_creds"] is None


@pytest.mark.asyncio
async def test_exchange_marks_token_consumed(client, engine, seeded):
    tok_bound, _ = seeded
    r = await client.post("/api/cloud-init/exchange-token", headers=_bearer(tok_bound))
    assert r.status_code == 200

    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        item = (
            await s.execute(select(DeploymentItem).where(DeploymentItem.id == 101))
        ).scalar_one()
    assert item.token_consumed_at is not None


@pytest.mark.asyncio
async def test_exchange_replay_is_410_gone(client, seeded):
    tok_bound, _ = seeded
    r1 = await client.post("/api/cloud-init/exchange-token", headers=_bearer(tok_bound))
    assert r1.status_code == 200

    r2 = await client.post("/api/cloud-init/exchange-token", headers=_bearer(tok_bound))
    assert r2.status_code == 410
    assert "already consumed" in r2.json()["detail"]


@pytest.mark.asyncio
async def test_exchange_unknown_token_is_401(client, seeded):
    r = await client.post("/api/cloud-init/exchange-token", headers=_bearer("tok_does-not-exist"))
    assert r.status_code == 401
    # The error message must NOT distinguish unknown-hash vs. no-bearer —
    # both leak DB-membership info to an attacker.
    assert r.json()["detail"] == "unknown token"


@pytest.mark.asyncio
async def test_exchange_missing_bearer_is_401(client):
    r = await client.post("/api/cloud-init/exchange-token")
    assert r.status_code == 401
    assert r.headers.get("www-authenticate") == "Bearer"


@pytest.mark.asyncio
async def test_exchange_empty_bearer_is_401(client):
    r = await client.post("/api/cloud-init/exchange-token", headers={"Authorization": "Bearer "})
    assert r.status_code == 401
