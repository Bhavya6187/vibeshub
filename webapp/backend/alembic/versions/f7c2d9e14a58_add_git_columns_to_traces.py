"""add git branch/commit columns to traces

Revision ID: f7c2d9e14a58
Revises: e1f8a2b9c073
Create Date: 2026-07-25 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f7c2d9e14a58"
down_revision: Union[str, Sequence[str], None] = "e1f8a2b9c073"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "traces", sa.Column("git_branch", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "traces", sa.Column("git_commit", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("traces", "git_commit")
    op.drop_column("traces", "git_branch")
