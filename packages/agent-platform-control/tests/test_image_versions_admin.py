"""/admin/image-versions endpoints (PR-E E-1 + E-3, decisions 12 + 13)."""

from __future__ import annotations

import base64

import pytest
from agent_platform_control import config
from agent_platform_control.db.models import ImageVersion
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from sqlalchemy.ext.asyncio import async_sessionmaker

ADMIN = {"Authorization": "Bearer dev-admin-token-CHANGE-ME"}


# ---------------------------------------------------------- key + sig helpers

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUBLIC_PEM = _PRIVATE_KEY.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
)


def _sign(message: str) -> str:
    raw = _PRIVATE_KEY.sign(message.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(raw).decode("ascii")


_OVA_SHA256 = "a" * 64  # fake hex digest; verification works over the literal bytes


@pytest.fixture(autouse=True)
def _wire_signing_pubkey(monkeypatch):
    """Inject the test public key into Settings for every test."""
    monkeypatch.setenv(
        "AGENT_PLATFORM_IMAGE_SIGNING_PUBKEY_PEM",
        _PUBLIC_PEM.decode("utf-8"),
    )
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def _body(version: str = "v0.1.0", *, signature_b64: str | None = None) -> dict:
    return {
        "version": version,
        "ova_sha256": _OVA_SHA256,
        "signature_b64": signature_b64 or _sign(_OVA_SHA256),
        "signed_by": "agent-platform-release",
        "template_path": (
            "[ds] templates/agent-platform-base-v0.1.0/agent-platform-base-v0.1.0.vmtx"
        ),
        "notes": "initial release",
    }


# ----------------------------------------------------------------- register


@pytest.mark.asyncio
async def test_register_happy_path(client):
    r = await client.post("/admin/image-versions", headers=ADMIN, json=_body())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["version"] == "v0.1.0"
    assert body["ova_sha256"] == _OVA_SHA256
    assert body["is_current"] is False


@pytest.mark.asyncio
async def test_register_requires_admin(client):
    r = await client.post("/admin/image-versions", json=_body())
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_register_rejects_bad_signature(client):
    body = _body(signature_b64=_sign("wrong-message"))
    r = await client.post("/admin/image-versions", headers=ADMIN, json=body)
    assert r.status_code == 422
    detail = r.json()["detail"].lower()
    assert "invalid signature" in detail


@pytest.mark.asyncio
async def test_register_rejects_duplicate_version(client):
    r1 = await client.post("/admin/image-versions", headers=ADMIN, json=_body())
    assert r1.status_code == 201
    r2 = await client.post("/admin/image-versions", headers=ADMIN, json=_body())
    assert r2.status_code == 409
    assert "already registered" in r2.json()["detail"]


@pytest.mark.asyncio
async def test_register_rejects_non_hex_sha256(client):
    body = _body()
    body["ova_sha256"] = "not-hex-at-all-not-hex-at-all-not-hex-at-all-not-hex-at-all-aaaa"
    r = await client.post("/admin/image-versions", headers=ADMIN, json=body)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_register_503_when_pubkey_unconfigured(client, monkeypatch):
    monkeypatch.setenv("AGENT_PLATFORM_IMAGE_SIGNING_PUBKEY_PEM", "")
    config.get_settings.cache_clear()
    r = await client.post("/admin/image-versions", headers=ADMIN, json=_body())
    assert r.status_code == 503
    assert "not configured" in r.json()["detail"]


# ----------------------------------------------------------------- promote


@pytest.mark.asyncio
async def test_promote_makes_target_current(client, engine):
    r1 = await client.post("/admin/image-versions", headers=ADMIN, json=_body("v0.1.0"))
    iv_id = r1.json()["id"]
    r2 = await client.post(f"/admin/image-versions/{iv_id}/promote", headers=ADMIN)
    assert r2.status_code == 200
    assert r2.json()["is_current"] is True


@pytest.mark.asyncio
async def test_promote_demotes_previous_current(client, engine):
    # Register two; promote second; first should no longer be current
    r1 = await client.post("/admin/image-versions", headers=ADMIN, json=_body("v0.1.0"))
    id1 = r1.json()["id"]
    await client.post(f"/admin/image-versions/{id1}/promote", headers=ADMIN)

    r2 = await client.post("/admin/image-versions", headers=ADMIN, json=_body("v0.2.0"))
    id2 = r2.json()["id"]
    await client.post(f"/admin/image-versions/{id2}/promote", headers=ADMIN)

    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        first = await s.get(ImageVersion, id1)
        second = await s.get(ImageVersion, id2)
    assert first.is_current is False
    assert second.is_current is True


@pytest.mark.asyncio
async def test_promote_404_when_id_unknown(client):
    r = await client.post("/admin/image-versions/9999/promote", headers=ADMIN)
    assert r.status_code == 404


# ----------------------------------------------------------------- list


@pytest.mark.asyncio
async def test_list_returns_newest_first(client):
    await client.post("/admin/image-versions", headers=ADMIN, json=_body("v0.1.0"))
    await client.post("/admin/image-versions", headers=ADMIN, json=_body("v0.2.0"))
    r = await client.get("/admin/image-versions", headers=ADMIN)
    assert r.status_code == 200
    items = r.json()
    assert [it["version"] for it in items] == ["v0.2.0", "v0.1.0"]
