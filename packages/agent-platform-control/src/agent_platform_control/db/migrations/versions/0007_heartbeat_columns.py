"""vms.last_heartbeat_at + deployment_items.heartbeat_token_hash (Decision 16 PR-D)

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-29

Decision 16 (locked 2026-05-28): a sweeper marks VMs unhealthy at 5min
without a heartbeat and lost at 30min. Decision 9: per-VM secrets
(including the heartbeat token) live in Vaultwarden + their hash on
deployment_items for server-side lookup.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vms",
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "deployment_items",
        sa.Column("heartbeat_token_hash", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_deployment_items_heartbeat_token_hash",
        "deployment_items",
        ["heartbeat_token_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_deployment_items_heartbeat_token_hash", "deployment_items")
    op.drop_column("deployment_items", "heartbeat_token_hash")
    op.drop_column("vms", "last_heartbeat_at")
