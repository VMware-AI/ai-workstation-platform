"""deployments + deployment_items — batch provisioning

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deployments",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "requested_by",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("template", sa.String(255), nullable=False),
        sa.Column("image_version", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("requested_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("succeeded_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_deployments_tenant_id", "deployments", ["tenant_id"])
    op.create_index("ix_deployments_state", "deployments", ["state"])

    op.create_table(
        "deployment_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "deployment_id",
            sa.String(64),
            sa.ForeignKey("deployments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "owner_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("intended_name", sa.String(255), nullable=False),
        sa.Column(
            "vm_id", sa.String(64), sa.ForeignKey("vms.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("state", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("user_token_enc", sa.Text(), nullable=False),
        sa.Column("user_token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_deployment_items_deployment_id", "deployment_items", ["deployment_id"])
    op.create_index("ix_deployment_items_state", "deployment_items", ["state"])
    op.create_index(
        "ix_deployment_items_user_token_hash", "deployment_items", ["user_token_hash"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_deployment_items_user_token_hash", table_name="deployment_items")
    op.drop_index("ix_deployment_items_state", table_name="deployment_items")
    op.drop_index("ix_deployment_items_deployment_id", table_name="deployment_items")
    op.drop_table("deployment_items")
    op.drop_index("ix_deployments_state", table_name="deployments")
    op.drop_index("ix_deployments_tenant_id", table_name="deployments")
    op.drop_table("deployments")
