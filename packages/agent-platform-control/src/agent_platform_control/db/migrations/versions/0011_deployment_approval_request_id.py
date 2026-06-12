"""deployments.approval_request_id + unique index (PR-review #129)

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-06

One approval provisions at most one deployment. The reverse link previously
lived only in the JSON ``extra`` column and was enforced by a non-atomic
check-then-insert (TOCTOU): two concurrent POSTs could both create a
deployment and double-spend quota. A dedicated nullable column with a UNIQUE
index makes the second insert fail at the DB level (caught → 409). NULL is
allowed for non-approval deployments (both SQLite and PostgreSQL treat NULLs
as distinct in a unique index).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deployments",
        sa.Column("approval_request_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "uq_deployments_approval_request_id",
        "deployments",
        ["approval_request_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_deployments_approval_request_id", table_name="deployments")
    op.drop_column("deployments", "approval_request_id")
