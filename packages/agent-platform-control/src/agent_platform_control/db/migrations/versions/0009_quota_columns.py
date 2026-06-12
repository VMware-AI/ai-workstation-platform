"""users.quota_vms + tenants.quota_vms_per_user (Decision 15 PR-E)

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-29

Three-layer per-user VM count quota: user.quota_vms (top) →
tenant.quota_vms_per_user → global default (orchestrator.quota.
DEFAULT_VMS_PER_USER = 3). Both new columns are NULL by default so the
migration is additive.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("quota_vms", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "tenants",
        sa.Column("quota_vms_per_user", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenants", "quota_vms_per_user")
    op.drop_column("users", "quota_vms")
