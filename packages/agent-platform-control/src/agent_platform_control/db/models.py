"""SQLAlchemy 2.0 ORM — 6 core tables.

Schema follows v2 design doc (docs/plans/2026-05-17-agent-platform-design.md §2).
Migrations under db/migrations/versions/.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # short kebab-case
    display_name: Mapped[str] = mapped_column(String(255))
    resource_pool: Mapped[str | None] = mapped_column(String(255))
    # Decision 15 PR-E: per-tenant override for the per-user VM count quota.
    # NULL = use the global default (orchestrator.quota.DEFAULT_VMS_PER_USER).
    quota_vms_per_user: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    users: Mapped[list[User]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    vms: Mapped[list[VM]] = relationship(back_populates="tenant", cascade="all, delete-orphan")


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # OIDC sub or login
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="user")  # user | admin | tenant-admin
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    quota_mb: Mapped[int] = mapped_column(BigInteger, default=50 * 1024)
    # OpenSSH-format public key uploaded by the user via the portal (decision 4 PR-C).
    # NULL = user hasn't uploaded one yet; provisioning writes empty authorized_keys
    # and the VM boots, just no SSH access until the user uploads.
    ssh_pubkey: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Decision 15 PR-E: per-user override (top of the 3-layer fallback).
    # NULL = inherit Tenant.quota_vms_per_user → global default 3.
    quota_vms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    tenant: Mapped[Tenant] = relationship(back_populates="users")
    vms: Mapped[list[VM]] = relationship(back_populates="owner", cascade="all, delete-orphan")


class ImageVersion(Base):
    __tablename__ = "image_versions"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    version: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # eg "v0.1.0"
    ova_sha256: Mapped[str] = mapped_column(String(64))
    signed_by: Mapped[str | None] = mapped_column(String(255))
    # Base64-encoded raw signature bytes (decision 12 PR-E). Verified against
    # AGENT_PLATFORM_IMAGE_SIGNING_PUBKEY_PEM at registration time + before every
    # clone (cached). NULL = legacy row from before PR-E; clone path treats
    # NULL as "registered before signature verification was enforced" and
    # logs a warning (admins should re-register to upgrade).
    signature_b64: Mapped[str | None] = mapped_column(Text)
    template_path: Mapped[str | None] = mapped_column(String(255))  # vCenter inventory path
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    is_current: Mapped[bool] = mapped_column(default=False)


class VM(Base):
    __tablename__ = "vms"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # vCenter MoRef or our UUID
    name: Mapped[str] = mapped_column(String(255), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    image_version: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(
        String(32), default="provisioning"
    )  # provisioning|running|stopping|retired
    ip_address: Mapped[str | None] = mapped_column(String(45))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Decision 16 PR-D: last_heartbeat_at drives the 5min / 30min sweeper.
    # NULL = VM has never reported (boot still in flight) — sweeper ignores.
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tenant: Mapped[Tenant] = relationship(back_populates="vms")
    owner: Mapped[User] = relationship(back_populates="vms")

    __table_args__ = (Index("ix_vms_tenant_state", "tenant_id", "state"),)


class Deployment(Base):
    """A batch VM provisioning request.

    States: pending -> running -> {completed, partially_failed, cancelled}
    Counts are denormalized from DeploymentItem rows for cheap list rendering.
    """

    __tablename__ = "deployments"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # uuid4 hex
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    template: Mapped[str] = mapped_column(String(255))  # vCenter template inventory path
    image_version: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    requested_count: Mapped[int] = mapped_column(default=0)
    succeeded_count: Mapped[int] = mapped_column(default=0)
    failed_count: Mapped[int] = mapped_column(default=0)
    cancelled_count: Mapped[int] = mapped_column(default=0, server_default="0")
    # One approval → at most one deployment. UNIQUE so a concurrent double
    # POST /from-approval can't create two (PR-review #129). NULL for
    # non-approval deployments.
    approval_request_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, unique=True, index=True
    )
    extra: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    items: Mapped[list[DeploymentItem]] = relationship(
        back_populates="deployment", cascade="all, delete-orphan", order_by="DeploymentItem.id"
    )


class DeploymentItem(Base):
    """Per-VM row inside a deployment batch.

    States: pending -> cloning -> customizing -> powered_on
                              \\-> failed
            pending -> cancelled
    """

    __tablename__ = "deployment_items"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    deployment_id: Mapped[str] = mapped_column(
        ForeignKey("deployments.id", ondelete="CASCADE"), index=True
    )
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    intended_name: Mapped[str] = mapped_column(String(255))
    vm_id: Mapped[str | None] = mapped_column(ForeignKey("vms.id", ondelete="SET NULL"))
    state: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(default=0)
    user_token_enc: Mapped[str] = mapped_column(
        Text
    )  # encrypted token, decrypted only for clone-time OVF injection
    user_token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True
    )  # sha256(token), used for future heartbeat lookup
    # When the in-VM cloud-init first redeemed the token. NULL = never redeemed.
    # Single-use semantics: a second redeem attempt returns 410 Gone (Task 1.20.3).
    token_consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When the control plane handed extraConfig to vCenter. Drives the
    # decision-8 30-minute TTL: now() - token_issued_at > 30min AND not consumed
    # → worker marks the item failed and the cleanup cron eventually
    # destroys the VM (decision 5). NULL on rows older than PR-D = "no clock
    # started" → never expires (backwards-compat for in-flight deployments).
    token_issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Hash of the heartbeat_access_token issued by secret_provisioner (PR-C).
    # The heartbeat endpoint hashes the bearer + looks up the item by this
    # column, then updates the bound VM's last_heartbeat_at (decision 16 PR-D).
    # NULL until the secret_provisioner sets it; sweeper ignores VMs whose
    # owning item has no token (boot still in flight).
    heartbeat_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    deployment: Mapped[Deployment] = relationship(back_populates="items")


class TokenUsage(Base):
    """Per-call usage row; mirrors LiteLLM SpendLogs but normalized to our user/tenant."""

    __tablename__ = "token_usage"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    agent: Mapped[str] = mapped_column(String(32))  # xiaoguai|goose|claude-code|qcoder
    model: Mapped[str] = mapped_column(String(64))
    in_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    out_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    # G1 (doc 37 §4.4): full-chain trace id (agent → gateway → metering).
    # Nullable for backward compat with pre-G1 events. 128 matches the platform
    # x-request-id contract (request_id.py / gateway _SANE_REQUEST_ID).
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    __table_args__ = (Index("ix_token_usage_user_ts", "user_id", "ts"),)


class Upgrade(Base):
    """A blue-green upgrade aggregate (Task 1.12).

    Each row tracks one tenant's upgrade from ``from_version`` → ``to_version``.
    State machine lives in ``orchestrator.upgrade_state``; this row only stores
    the current state name + a JSON plan blob.

    ``plan_json`` shape::

        {
          "vms": [{"green_vm_id": "vm-1", "owner_id": "alice",
                   "blue_vm_intended_name": "alice-blue-001"}],
          "estimated_seconds": 300,
          "home_volume_strategy": "copy"
        }
    """

    __tablename__ = "upgrades"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # uuid4 hex
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    from_version: Mapped[str] = mapped_column(String(64))
    to_version: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32), default="planned", index=True)
    plan_json: Mapped[dict | None] = mapped_column(JSON)
    started_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    vms: Mapped[list[UpgradeVM]] = relationship(
        back_populates="upgrade", cascade="all, delete-orphan", order_by="UpgradeVM.id"
    )


class UpgradeVM(Base):
    """Per-VM row inside an upgrade — tracks the blue and green sides.

    ``role`` is 'green' for the pre-existing VM, 'blue' for the new VM created
    during ``provisioning_blue``. We keep both rows so cleanup / rollback can
    reach either side without inferring from naming conventions.
    """

    __tablename__ = "upgrade_vms"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    upgrade_id: Mapped[str] = mapped_column(
        ForeignKey("upgrades.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(8))  # "blue" | "green"
    vm_id: Mapped[str | None] = mapped_column(String(64))  # None until blue is provisioned
    intended_name: Mapped[str] = mapped_column(String(255))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    upgrade: Mapped[Upgrade] = relationship(back_populates="vms")

    __table_args__ = (Index("ix_upgrade_vms_upgrade_role", "upgrade_id", "role"),)


class AuditView(Base):
    """Control-plane audit trail for destructive operations.

    Written by orchestrator.audit.record_audit (VM destroy today; clone /
    force-redeploy as they land) and read by /admin/events. Originally a
    placeholder mirror of vmware-policy's audit.db; the control plane now owns
    it directly since there is no separate policy mirror populating it.
    """

    __tablename__ = "audit_view"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(128), index=True)
    operation: Mapped[str] = mapped_column(String(128), index=True)
    resource: Mapped[str] = mapped_column(String(255))
    params: Mapped[dict | None] = mapped_column(JSON)
    result: Mapped[str] = mapped_column(String(32))  # success|failure|denied
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
