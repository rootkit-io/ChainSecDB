"""Add extraction provenance and link findings to their document's run."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_extraction_runs"
down_revision: str | None = "0002_findings_taxonomy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "extraction_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("raw_document_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(255), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("prompt_version", sa.String(255), nullable=False),
        sa.Column("schema_version", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("failure_message", sa.String(500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["raw_document_id"], ["raw_documents.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("id", "raw_document_id", name="uq_extraction_runs_id_document"),
        sa.CheckConstraint("provider ~ '[^[:space:]]'", name="ck_extraction_runs_provider"),
        sa.CheckConstraint("model ~ '[^[:space:]]'", name="ck_extraction_runs_model"),
        sa.CheckConstraint(
            "prompt_version ~ '[^[:space:]]'", name="ck_extraction_runs_prompt_version"
        ),
        sa.CheckConstraint(
            "schema_version ~ '[^[:space:]]'", name="ck_extraction_runs_schema_version"
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')",
            name="ck_extraction_runs_status",
        ),
        sa.CheckConstraint(
            "(status = 'PENDING' AND started_at IS NULL AND completed_at IS NULL) OR "
            "(status = 'RUNNING' AND started_at IS NOT NULL AND completed_at IS NULL) OR "
            "(status IN ('SUCCEEDED', 'FAILED') AND started_at IS NOT NULL "
            "AND completed_at IS NOT NULL)",
            name="ck_extraction_runs_lifecycle",
        ),
        sa.CheckConstraint("completed_at >= started_at", name="ck_extraction_runs_timestamp_order"),
        sa.CheckConstraint(
            "status = 'FAILED' OR (failure_code IS NULL AND failure_message IS NULL)",
            name="ck_extraction_runs_failure_state",
        ),
    )
    op.create_index("ix_extraction_runs_raw_document_id", "extraction_runs", ["raw_document_id"])
    op.add_column("security_findings", sa.Column("extraction_run_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_security_findings_extraction_document",
        "security_findings",
        "extraction_runs",
        ["extraction_run_id", "raw_document_id"],
        ["id", "raw_document_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_security_findings_extraction_run_id", "security_findings", ["extraction_run_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_security_findings_extraction_run_id", table_name="security_findings")
    op.drop_constraint(
        "fk_security_findings_extraction_document", "security_findings", type_="foreignkey"
    )
    op.drop_column("security_findings", "extraction_run_id")
    op.drop_index("ix_extraction_runs_raw_document_id", table_name="extraction_runs")
    op.drop_table("extraction_runs")
