"""Create raw_documents."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_raw_documents"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "raw_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_name", sa.String(255), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("document_type", sa.String(255), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.CHAR(64), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_hash", name="uq_raw_documents_content_hash"),
    )


def downgrade() -> None:
    op.drop_table("raw_documents")
