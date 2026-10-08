"""Add manually supplied findings and exact source evidence."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_findings_taxonomy"
down_revision: str | None = "0001_raw_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "security_findings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("raw_document_id", sa.Uuid(), nullable=False),
        sa.Column("source_finding_id", sa.String(255), nullable=True),
        sa.Column("source_title", sa.String(500), nullable=True),
        sa.Column("source_severity", sa.String(255), nullable=True),
        sa.Column("source_category", sa.String(255), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("canonical_category", sa.String(40), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("root_cause", sa.Text(), nullable=True),
        sa.Column("impact", sa.Text(), nullable=True),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column("affected_contract", sa.String(255), nullable=True),
        sa.Column("affected_function", sa.String(255), nullable=True),
        sa.Column("source_file", sa.String(1024), nullable=True),
        sa.Column("line_start", sa.Integer(), nullable=True),
        sa.Column("line_end", sa.Integer(), nullable=True),
        sa.Column("protocol_name", sa.String(255), nullable=True),
        sa.Column("language", sa.String(255), nullable=True),
        sa.Column(
            "verification_status", sa.String(16), server_default="UNREVIEWED", nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["raw_document_id"], ["raw_documents.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "raw_document_id", "source_finding_id", name="uq_security_findings_document_source_id"
        ),
        sa.CheckConstraint("title ~ '[^[:space:]]'", name="ck_security_findings_title"),
        sa.CheckConstraint("line_start > 0", name="ck_security_findings_line_start"),
        sa.CheckConstraint("line_end > 0", name="ck_security_findings_line_end"),
        sa.CheckConstraint("line_end >= line_start", name="ck_security_findings_line_range"),
        sa.CheckConstraint(
            "severity IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFORMATIONAL', 'UNKNOWN')",
            name="ck_security_findings_severity",
        ),
        sa.CheckConstraint(
            "canonical_category IN ('ACCESS_CONTROL', 'AUTH_SIGNATURE', 'REENTRANCY', "
            "'ORACLE_PRICE_MANIPULATION', 'ACCOUNTING', 'ARITHMETIC_PRECISION', 'BUSINESS_LOGIC', "
            "'STATE_TRANSITION', 'EXTERNAL_CALL', 'TOKEN_INTEGRATION', 'FRONTRUNNING_MEV', "
            "'DENIAL_OF_SERVICE', 'UPGRADEABILITY_INITIALIZATION', 'GOVERNANCE', "
            "'CROSS_CHAIN_BRIDGE', 'LIQUIDATION', 'ECONOMIC_ATTACK', 'RANDOMNESS_TIME', "
            "'INPUT_VALIDATION', 'OTHER')",
            name="ck_security_findings_canonical_category",
        ),
        sa.CheckConstraint(
            "verification_status IN ('UNREVIEWED', 'VERIFIED', 'REJECTED')",
            name="ck_security_findings_verification_status",
        ),
    )
    op.create_table(
        "finding_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("finding_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_type", sa.String(16), server_default="SOURCE_TEXT", nullable=False),
        sa.Column("field_name", sa.String(32), nullable=True),
        sa.Column("source_excerpt", sa.Text(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["finding_id"], ["security_findings.id"], ondelete="CASCADE"),
        sa.CheckConstraint("start_offset >= 0", name="ck_finding_evidence_start_offset"),
        sa.CheckConstraint("end_offset > start_offset", name="ck_finding_evidence_offset_range"),
        sa.CheckConstraint(
            "evidence_type IN ('SOURCE_TEXT', 'CODE_REFERENCE', 'OTHER')",
            name="ck_finding_evidence_type",
        ),
        sa.CheckConstraint(
            "field_name IN ('title', 'severity', 'canonical_category', 'summary', 'root_cause', "
            "'impact', 'recommendation', 'affected_contract', 'affected_function', 'source_file', "
            "'line_start', 'line_end', 'protocol_name', 'language')",
            name="ck_finding_evidence_field_name",
        ),
    )
    op.create_index("ix_finding_evidence_finding_id", "finding_evidence", ["finding_id"])


def downgrade() -> None:
    op.drop_index("ix_finding_evidence_finding_id", table_name="finding_evidence")
    op.drop_table("finding_evidence")
    op.drop_table("security_findings")
