"""deployments.cancelled_count (PR-review #81 cancel terminality)

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-06

Denormalized count of items cancelled via POST /deployments/{id}/cancel, so
the worker's terminality test (succeeded + failed + cancelled >= requested)
can reach a terminal state when some items were cancelled rather than
clone-completed. Additive, NOT NULL with a 0 server default.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deployments",
        sa.Column(
            "cancelled_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("deployments", "cancelled_count")
