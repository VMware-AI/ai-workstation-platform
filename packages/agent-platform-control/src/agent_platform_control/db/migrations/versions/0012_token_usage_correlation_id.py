"""token_usage.correlation_id (doc 37 §4.4 · G1)

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-10

Full-chain trace id threading agent → gateway → metering so a token_usage
row can be joined back to the originating call (and the audit event) by
correlation-id. Nullable for backward compat with pre-G1 events; indexed for
trace lookups.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "token_usage",
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "ix_token_usage_correlation_id",
        "token_usage",
        ["correlation_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_token_usage_correlation_id", table_name="token_usage")
    op.drop_column("token_usage", "correlation_id")
