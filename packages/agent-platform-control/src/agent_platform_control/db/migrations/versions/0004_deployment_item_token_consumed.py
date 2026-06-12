"""deployment_items.token_consumed_at — single-use cloud-init token (Task 1.20.3)

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deployment_items",
        sa.Column("token_consumed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("deployment_items", "token_consumed_at")
