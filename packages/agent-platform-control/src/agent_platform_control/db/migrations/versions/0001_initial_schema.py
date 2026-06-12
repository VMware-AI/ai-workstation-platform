"""initial schema — 6 core tables

Revision ID: 0001
Revises:
Create Date: 2026-05-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("resource_pool", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="user"),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("quota_mb", sa.BigInteger, nullable=False, server_default="51200"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_table(
        "image_versions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("version", sa.String(64), nullable=False, unique=True),
        sa.Column("ova_sha256", sa.String(64), nullable=False),
        sa.Column("signed_by", sa.String(255)),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_current", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_image_versions_version", "image_versions", ["version"], unique=True)
    op.create_table(
        "vms",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "owner_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("image_version", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="provisioning"),
        sa.Column("ip_address", sa.String(45)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_vms_name", "vms", ["name"])
    op.create_index("ix_vms_tenant_state", "vms", ["tenant_id", "state"])
    op.create_table(
        "token_usage",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("in_tokens", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("out_tokens", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("duration_s", sa.Float, nullable=False, server_default="0"),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_token_usage_user_id", "token_usage", ["user_id"])
    op.create_index("ix_token_usage_tenant_id", "token_usage", ["tenant_id"])
    op.create_index("ix_token_usage_ts", "token_usage", ["ts"])
    op.create_index("ix_token_usage_user_ts", "token_usage", ["user_id", "ts"])
    op.create_table(
        "audit_view",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("operation", sa.String(128), nullable=False),
        sa.Column("resource", sa.String(255), nullable=False),
        sa.Column("params", sa.JSON),
        sa.Column("result", sa.String(32), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_view_actor", "audit_view", ["actor"])
    op.create_index("ix_audit_view_operation", "audit_view", ["operation"])
    op.create_index("ix_audit_view_ts", "audit_view", ["ts"])


def downgrade() -> None:
    for t in ("audit_view", "token_usage", "vms", "image_versions", "users", "tenants"):
        op.drop_table(t)
