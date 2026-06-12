"""users.ssh_pubkey — portal-uploaded OpenSSH public key (Decision 4, PR-C)

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-29

NULL = user hasn't uploaded one yet. cloud-init provisioning treats absence
as "boot the VM, but with empty authorized_keys" — user uploads later and
re-provisions, M2 path will hot-rotate.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("ssh_pubkey", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "ssh_pubkey")
