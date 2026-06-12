"""/api/me/* — per-user endpoints for the C12 portal.

Returns data owned by the authenticated caller (not require_admin).
Schema mirrors `packages/agent-platform-portal/src/lib/api.ts`:
  GET /api/me/instances → { agents: [...] }
  GET /api/me/usage?days=N → { days: [...], total_tokens }
"""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, date, datetime, timedelta

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import CurrentUser, get_current_user
from ..config import get_settings
from ..db.models import VM, TokenUsage, User
from ..db.session import get_session
from ..db.tenant_scope import tenant_scope

router = APIRouter(prefix="/api/me", tags=["me"])


# Map VM.state → portal-facing agent state.
# VM lifecycle: provisioning | running | stopping | retired
# Portal states: pending     | running | running   | (filtered out)
_VM_STATE_TO_AGENT_STATE: dict[str, str] = {
    "provisioning": "pending",
    "running": "running",
    "stopping": "running",
}


# Field names below are camelCase to match the portal's TypeScript wire contract
# (packages/agent-platform-portal/src/lib/api.ts). N815 is intentionally suppressed.
class MyAgent(BaseModel):
    id: str
    name: str
    template: str
    state: str  # "pending" | "running" | "stopped" | "error"
    createdAt: datetime  # noqa: N815
    ttydUrl: str | None = None  # noqa: N815
    # M1 W-5: UNC path under the per-user workspace dataset; null when
    # fileshare_base is unconfigured (dev) so the portal shows "—" not a
    # broken `\\\u\…\workspace` link.
    fileshare_path: str | None = None


class MyAgentList(BaseModel):
    agents: list[MyAgent]


class UsageDay(BaseModel):
    date: str  # YYYY-MM-DD
    tokens: int


class MyUsage(BaseModel):
    days: list[UsageDay]
    total_tokens: int
    # M1 W-5: soft quota; M2.2 will swap to real per-user limits from C5.
    # `quota_used` mirrors `total_tokens` so the portal progress bar has a
    # single source of truth (the portal already has total_tokens; we keep
    # both rather than diverge later).
    quota_total: int
    quota_used: int


class TtydUrl(BaseModel):
    """W-3.1: where the portal Terminal page should attach its xterm
    websocket. M1 returns a single mock URL configured per deployment;
    W-3.2 will compute per-VM signed wss:// URLs."""

    url: str


def _agent_state(vm_state: str) -> str:
    # Anything unexpected becomes "error" so the portal surfaces it loudly
    # instead of silently misclassifying as running.
    return _VM_STATE_TO_AGENT_STATE.get(vm_state, "error")


@router.get("/instances", response_model=MyAgentList)
async def list_my_instances(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> MyAgentList:
    """Return non-retired VMs owned by the caller.

    Retired VMs are excluded — the portal lists "my agents", not history.
    `template` carries `VM.image_version` (no separate template column on
    the VM row); good enough for the portal's text display.
    """
    # Tenant scope first (Decision 14): even if owner_id check were bypassed,
    # this blocks cross-tenant reads. Per-user owner_id filter still applies.
    # Admin role has no tenant of their own and naturally owns no VMs — skip
    # the tenant filter so admin gets the empty list rather than a 500.
    base = (
        select(VM)
        .where(VM.owner_id == user.user_id)
        .where(VM.state != "retired")
        .order_by(VM.created_at.desc())
    )
    stmt = (
        base
        if user.role == "admin"
        else tenant_scope(base, tenant_id=user.tenant_id or "", model=VM)
    )
    rows = (await session.execute(stmt)).scalars().all()
    settings = get_settings()
    agents = [
        MyAgent(
            id=vm.id,
            name=vm.name,
            template=vm.image_version,
            state=_agent_state(vm.state),
            createdAt=vm.created_at,
            fileshare_path=_fileshare_path(settings.fileshare_base, vm.owner_id),
        )
        for vm in rows
    ]
    return MyAgentList(agents=agents)


def _fileshare_path(base: str, owner_id: str) -> str | None:
    """UNC path under the per-user dataset, or None if base is unset.

    Format mirrors what cloud-init will mount in the VM:
        \\\\<base>\\u\\<owner>\\workspace
    """
    if not base:
        return None
    return rf"\\{base}\u\{owner_id}\workspace"


@router.get("/usage", response_model=MyUsage)
async def my_usage(
    days: int = Query(default=30, ge=1, le=365),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> MyUsage:
    """Aggregate token usage for the caller over the last `days` calendar days.

    Returns a zero-filled timeline so the portal chart renders without gaps.
    `tokens` per day = sum(in_tokens + out_tokens).
    """
    today = datetime.now(UTC).date()
    start_day = today - timedelta(days=days - 1)
    start_dt = datetime.combine(start_day, datetime.min.time(), tzinfo=UTC)

    day_col = func.date(TokenUsage.ts).label("day")
    base = (
        select(
            day_col,
            func.coalesce(func.sum(TokenUsage.in_tokens + TokenUsage.out_tokens), 0).label(
                "tokens"
            ),
        )
        .where(TokenUsage.user_id == user.user_id)
        .where(TokenUsage.ts >= start_dt)
        .group_by(day_col)
    )
    # Admin role bypass (see list_my_instances for rationale).
    stmt = (
        base
        if user.role == "admin"
        else tenant_scope(base, tenant_id=user.tenant_id or "", model=TokenUsage)
    )
    rows = (await session.execute(stmt)).all()
    by_day: dict[str, int] = {str(r.day): int(r.tokens) for r in rows}

    timeline: list[UsageDay] = []
    total = 0
    for offset in range(days):
        d: date = start_day + timedelta(days=offset)
        key = d.isoformat()
        tokens = by_day.get(key, 0)
        timeline.append(UsageDay(date=key, tokens=tokens))
        total += tokens

    settings = get_settings()
    return MyUsage(
        days=timeline,
        total_tokens=total,
        quota_total=settings.quota_total_tokens,
        quota_used=total,
    )


# --------------------------------------------------------- SSH pubkey upload
# Decision 4 PR-C: the SSH public key the user wants on their VMs lives in
# users.ssh_pubkey. Every new VM provisioning reads this column and copies
# it into the per-VM Vaultwarden entry (vm/{item_id}/ssh_authorized_keys).

# 4 KB cap is generous — even an RSA-8192 OpenSSH pubkey lands well under 2 KB.
# Bigger inputs are almost certainly garbage; reject before parsing.
_PUBKEY_MAX_BYTES = 4096


class SshPubkeyOut(BaseModel):
    pubkey: str | None
    fingerprint: str | None  # OpenSSH-style "SHA256:..." or None


class SshPubkeyIn(BaseModel):
    pubkey: str = Field(min_length=1, max_length=_PUBKEY_MAX_BYTES)


def _fingerprint(pubkey: str) -> str:
    """OpenSSH-compatible SHA256 fingerprint (matches ``ssh-keygen -lf``)."""
    parsed = serialization.load_ssh_public_key(pubkey.encode())
    wire = parsed.public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    )
    # "ssh-ed25519 BASE64 [comment]" — fingerprint is over the binary blob only
    parts = wire.decode("ascii").split(None, 2)
    if len(parts) < 2:
        raise ValueError("could not re-encode pubkey for fingerprint")
    binary = base64.b64decode(parts[1])
    digest = hashlib.sha256(binary).digest()
    return "SHA256:" + base64.b64encode(digest).rstrip(b"=").decode("ascii")


def _validate_pubkey(raw: str) -> None:
    """Reject private keys + non-OpenSSH inputs with an educational 400."""
    if "PRIVATE KEY" in raw.upper():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "That looks like a PRIVATE key — paste your PUBLIC key instead "
                "(starts with 'ssh-ed25519' / 'ssh-rsa' / 'ecdsa-sha2-...'). "
                "Find it in ~/.ssh/id_ed25519.pub or run `ssh-keygen -y -f <privkey>`."
            ),
        )
    try:
        serialization.load_ssh_public_key(raw.encode())
    except (ValueError, UnsupportedAlgorithm) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Not a valid OpenSSH public key: {exc}. "
                f"Expected format 'ssh-ed25519 AAAA... [comment]'."
            ),
        ) from exc


@router.get("/ssh-pubkey", response_model=SshPubkeyOut)
async def get_my_ssh_pubkey(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SshPubkeyOut:
    """Return the caller's stored SSH public key + its fingerprint.

    Returns ``{pubkey: null, fingerprint: null}`` if the user has not uploaded
    one yet — the portal shows an empty state and prompts the user to add one.
    """
    db_user = await session.get(User, user.user_id)
    if db_user is None or db_user.ssh_pubkey is None:
        return SshPubkeyOut(pubkey=None, fingerprint=None)
    return SshPubkeyOut(
        pubkey=db_user.ssh_pubkey,
        fingerprint=_fingerprint(db_user.ssh_pubkey),
    )


@router.put("/ssh-pubkey", response_model=SshPubkeyOut)
async def put_my_ssh_pubkey(
    body: SshPubkeyIn,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SshPubkeyOut:
    """Overwrite the caller's stored SSH public key.

    Validation: must parse as an OpenSSH public key; private keys + garbage
    are rejected with a teaching 400 that points at common fixes.
    Existing VMs are NOT updated — the new key takes effect on next clone.
    """
    _validate_pubkey(body.pubkey)
    db_user = await session.get(User, user.user_id)
    if db_user is None:
        # Unknown user with valid auth header — possible in M1 stub if the
        # users table was seeded after the portal session started. 404 keeps
        # the failure mode distinct from "you're not logged in".
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"user {user.user_id} not in directory",
        )
    db_user.ssh_pubkey = body.pubkey
    await session.commit()
    return SshPubkeyOut(pubkey=body.pubkey, fingerprint=_fingerprint(body.pubkey))


@router.get("/instances/{vm_id}/ttyd-url", response_model=TtydUrl)
async def my_instance_ttyd_url(
    vm_id: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TtydUrl:
    """Return a websocket URL the portal Terminal page should attach to.

    Two modes (toggle via ``AGENT_PLATFORM_TTYD_REAL_MODE``):

    - **real_mode=True** (W-3.2): returns ``ws://{vm.ip_address}:{port}/ws``
      derived from the VM row's ``ip_address``. The portal connects
      directly to the VM's cloud-init-installed ttyd. M1 trusts the NSX
      segment for isolation; the scheme is plain ``ws://`` because ttyd's
      systemd unit does not terminate TLS — the portal must run on http
      (dev) or behind a reverse-proxy that proxies wss → ws (staging).
      W-3.3 follow-up adds an HMAC token sidecar + TLS termination.
    - **real_mode=False** (W-3.1 fallback): returns ``settings.ttyd_mock_url``
      so dev can demo against ``docker run tsl0922/ttyd`` without standing
      up VMs.

    Owner check is always real, so cross-user probing fails 404 in both
    modes.
    """
    base = select(VM).where(VM.id == vm_id, VM.owner_id == user.user_id)
    stmt = (
        base
        if user.role == "admin"
        else tenant_scope(base, tenant_id=user.tenant_id or "", model=VM)
    )
    vm = (await session.execute(stmt)).scalar_one_or_none()
    if vm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"vm_id={vm_id!r} not found for caller. "
                "Either the VM does not exist, was retired, or belongs to "
                "another owner."
            ),
        )
    settings = get_settings()
    if settings.ttyd_real_mode:
        if not vm.ip_address:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    f"vm_id={vm_id!r} has no IP yet — provisioning has not "
                    "finished. Retry once /api/me/instances reports an ip "
                    "for this VM."
                ),
            )
        return TtydUrl(url=f"ws://{vm.ip_address}:{settings.ttyd_port}/ws")
    if not settings.ttyd_mock_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "ttyd is not configured. Either set AGENT_PLATFORM_TTYD_REAL_MODE=true "
                "(VMs reachable via ip_address) or AGENT_PLATFORM_TTYD_MOCK_URL "
                "(dev demo against docker run tsl0922/ttyd)."
            ),
        )
    return TtydUrl(url=settings.ttyd_mock_url)
