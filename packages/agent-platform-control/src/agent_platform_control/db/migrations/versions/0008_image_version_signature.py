"""image_versions.signature_b64 + template_path (decision 12 PR-E)

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "image_versions",
        sa.Column("signature_b64", sa.Text(), nullable=True),
    )
    op.add_column(
        "image_versions",
        sa.Column("template_path", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("image_versions", "template_path")
    op.drop_column("image_versions", "signature_b64")
