"""upgrades + upgrade_vms — blue-green upgrade tracking (Task 1.12)

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "upgrades",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_version", sa.String(64), nullable=False),
        sa.Column("to_version", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="planned"),
        sa.Column("plan_json", sa.JSON(), nullable=True),
        sa.Column(
            "started_by",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_upgrades_tenant_id", "upgrades", ["tenant_id"])
    op.create_index("ix_upgrades_state", "upgrades", ["state"])

    op.create_table(
        "upgrade_vms",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "upgrade_id",
            sa.String(64),
            sa.ForeignKey("upgrades.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(8), nullable=False),
        sa.Column("vm_id", sa.String(64), nullable=True),
        sa.Column("intended_name", sa.String(255), nullable=False),
        sa.Column(
            "owner_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_upgrade_vms_upgrade_id", "upgrade_vms", ["upgrade_id"])
    op.create_index("ix_upgrade_vms_upgrade_role", "upgrade_vms", ["upgrade_id", "role"])


def downgrade() -> None:
    op.drop_index("ix_upgrade_vms_upgrade_role", table_name="upgrade_vms")
    op.drop_index("ix_upgrade_vms_upgrade_id", table_name="upgrade_vms")
    op.drop_table("upgrade_vms")
    op.drop_index("ix_upgrades_state", table_name="upgrades")
    op.drop_index("ix_upgrades_tenant_id", table_name="upgrades")
    op.drop_table("upgrades")
