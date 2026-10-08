from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.extraction_run import ExtractionRun  # noqa: F401
from app.db.models.finding_evidence import FindingEvidence
from app.taxonomy.categories import CanonicalCategory, Severity, VerificationStatus


class SecurityFinding(Base):
    __tablename__ = "security_findings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["extraction_run_id", "raw_document_id"],
            ["extraction_runs.id", "extraction_runs.raw_document_id"],
            name="fk_security_findings_extraction_document",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "raw_document_id", "source_finding_id", name="uq_security_findings_document_source_id"
        ),
        CheckConstraint("title ~ '[^[:space:]]'", name="ck_security_findings_title"),
        CheckConstraint("line_start > 0", name="ck_security_findings_line_start"),
        CheckConstraint("line_end > 0", name="ck_security_findings_line_end"),
        CheckConstraint("line_end >= line_start", name="ck_security_findings_line_range"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    raw_document_id: Mapped[UUID] = mapped_column(
        ForeignKey("raw_documents.id", ondelete="RESTRICT")
    )
    extraction_run_id: Mapped[UUID | None] = mapped_column(index=True)
    source_finding_id: Mapped[str | None] = mapped_column(String(255))
    source_title: Mapped[str | None] = mapped_column(String(500))
    source_severity: Mapped[str | None] = mapped_column(String(255))
    source_category: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(500))
    severity: Mapped[Severity] = mapped_column(
        Enum(
            Severity,
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=16,
            name="ck_security_findings_severity",
        )
    )
    canonical_category: Mapped[CanonicalCategory] = mapped_column(
        Enum(
            CanonicalCategory,
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=40,
            name="ck_security_findings_canonical_category",
        )
    )
    summary: Mapped[str | None] = mapped_column(Text)
    root_cause: Mapped[str | None] = mapped_column(Text)
    impact: Mapped[str | None] = mapped_column(Text)
    recommendation: Mapped[str | None] = mapped_column(Text)
    affected_contract: Mapped[str | None] = mapped_column(String(255))
    affected_function: Mapped[str | None] = mapped_column(String(255))
    source_file: Mapped[str | None] = mapped_column(String(1024))
    line_start: Mapped[int | None]
    line_end: Mapped[int | None]
    protocol_name: Mapped[str | None] = mapped_column(String(255))
    language: Mapped[str | None] = mapped_column(String(255))
    verification_status: Mapped[VerificationStatus] = mapped_column(
        Enum(
            VerificationStatus,
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=16,
            name="ck_security_findings_verification_status",
        ),
        default=VerificationStatus.UNREVIEWED,
        server_default="UNREVIEWED",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    evidence: Mapped[list[FindingEvidence]] = relationship(
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
        order_by=FindingEvidence.id,
    )
