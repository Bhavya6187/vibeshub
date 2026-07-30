"""add search_documents table

Revision ID: f4b2d6e8a1c3
Revises: e1f8a2b9c073
Create Date: 2026-07-18 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f4b2d6e8a1c3"
down_revision: Union[str, Sequence[str], None] = "e1f8a2b9c073"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create search_documents; Postgres also gets tsvector + GIN."""
    op.create_table(
        "search_documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("repo_full_name", sa.String(length=255), nullable=False),
        sa.Column(
            "trace_id",
            sa.Uuid(),
            sa.ForeignKey("traces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("anchor_uuid", sa.String(length=64), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("pr_url", sa.String(length=512), nullable=True),
        sa.Column(
            "is_private", sa.Boolean(), nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_search_documents_repo_full_name",
        "search_documents",
        ["repo_full_name"],
    )
    op.create_index(
        "ix_search_documents_trace_id", "search_documents", ["trace_id"]
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "ALTER TABLE search_documents ADD COLUMN search_tsv tsvector "
            "GENERATED ALWAYS AS (to_tsvector('english', "
            "coalesce(title, '') || ' ' || coalesce(body, ''))) STORED"
        )
        op.execute(
            "CREATE INDEX ix_search_documents_tsv ON search_documents "
            "USING GIN (search_tsv)"
        )


def downgrade() -> None:
    """Drop search_documents (tsvector column goes with the table)."""
    op.drop_index(
        "ix_search_documents_trace_id", table_name="search_documents"
    )
    op.drop_index(
        "ix_search_documents_repo_full_name", table_name="search_documents"
    )
    op.drop_table("search_documents")
