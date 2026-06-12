"""POST /v1/deployments — image_version signature gate (PR-E E-2, decision 12)."""

from __future__ import annotations

import base64

import pytest
import pytest_asyncio
from agent_platform_control import config
from agent_platform_control.db.models import ImageVersion, Tenant, User
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from sqlalchemy.ext.asyncio import async_sessionmaker

ADMIN = {"Authorization": "Bearer dev-admin-token-CHANGE-ME"}


_PRIVATE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUB_PEM = _PRIVATE.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
)


def _sign(message: str) -> str:
    raw = _PRIVATE.sign(message.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(raw).decode("ascii")


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch):
    monkeypatch.setenv("AGENT_PLATFORM_IMAGE_SIGNING_PUBKEY_PEM", _PUB_PEM.decode("utf-8"))
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


@pytest_asyncio.fixture
async def seeded(engine):
    """Tenant + alice + 3 image_versions: signed-valid, signed-tampered, legacy-unsigned."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        s.add(Tenant(id="t-a", display_name="A"))
        s.add(User(id="alice", email="a@x", display_name="A", tenant_id="t-a"))
        # signed + valid
        s.add(
            ImageVersion(
                version="v1.0.0",
                ova_sha256="a" * 64,
                signature_b64=_sign("a" * 64),
                signed_by="agent-platform-release",
            )
        )
        # signed but signature is wrong (signs a different hash)
        s.add(
            ImageVersion(
                version="v1.0.1",
                ova_sha256="b" * 64,
                signature_b64=_sign("c" * 64),  # mismatch
                signed_by="agent-platform-release",
            )
        )
        # legacy, no signature → accepted with warning
        s.add(
            ImageVersion(
                version="v0.9.0",
                ova_sha256="d" * 64,
                signature_b64=None,
            )
        )
        await s.commit()
    return sm


def _payload(version: str) -> dict:
    return {
        "tenant_id": "t-a",
        "template": "[t] agent-platform.vmtx",
        "image_version": version,
        "items": [{"owner_id": "alice", "intended_name": "vm-x"}],
    }


@pytest.mark.asyncio
async def test_create_passes_when_signature_valid(client, seeded):
    r = await client.post("/v1/deployments", headers=ADMIN, json=_payload("v1.0.0"))
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_create_rejects_tampered_signature(client, seeded):
    r = await client.post("/v1/deployments", headers=ADMIN, json=_payload("v1.0.1"))
    assert r.status_code == 422, r.text
    detail = r.json()["detail"].lower()
    assert "v1.0.1" in detail
    assert "invalid signature" in detail


@pytest.mark.asyncio
async def test_create_404_when_image_unregistered(client, seeded):
    r = await client.post("/v1/deployments", headers=ADMIN, json=_payload("v99.0.0"))
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert "v99.0.0" in detail
    assert "POST /admin/image-versions" in detail


@pytest.mark.asyncio
async def test_create_passes_for_legacy_unsigned_image(client, seeded):
    """Backwards-compat: rows from before PR-E (signature_b64=NULL) still work."""
    r = await client.post("/v1/deployments", headers=ADMIN, json=_payload("v0.9.0"))
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_create_503_when_pubkey_unconfigured_and_image_signed(client, seeded, monkeypatch):
    """If the pubkey is missing but the image_version IS signed, fail loud."""
    monkeypatch.setenv("AGENT_PLATFORM_IMAGE_SIGNING_PUBKEY_PEM", "")
    config.get_settings.cache_clear()
    r = await client.post("/v1/deployments", headers=ADMIN, json=_payload("v1.0.0"))
    assert r.status_code == 503
