"""deployment_items.token_issued_at — decision 8 bootstrap-token TTL clock

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-29

Decision 8 (locked 2026-05-28): bootstrap tokens get a 30-minute TTL whose
clock starts when the control plane injects extraConfig into vCenter. Add
the column NULL-by-default so in-flight rows from before this migration are
treated as "no clock started" — never expire (the existing single-use
semantic still protects them once cloud-init eventually redeems).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deployment_items",
        sa.Column("token_issued_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("deployment_items", "token_issued_at")
