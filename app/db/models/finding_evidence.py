from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.taxonomy.categories import EvidenceType, FindingField


class FindingEvidence(Base):
    __tablename__ = "finding_evidence"
    __table_args__ = (
        CheckConstraint("start_offset >= 0", name="ck_finding_evidence_start_offset"),
        CheckConstraint("end_offset > start_offset", name="ck_finding_evidence_offset_range"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    finding_id: Mapped[UUID] = mapped_column(
        ForeignKey("security_findings.id", ondelete="CASCADE"), index=True
    )
    evidence_type: Mapped[EvidenceType] = mapped_column(
        Enum(
            EvidenceType,
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=16,
            name="ck_finding_evidence_type",
        ),
        default=EvidenceType.SOURCE_TEXT,
        server_default="SOURCE_TEXT",
    )
    field_name: Mapped[FindingField | None] = mapped_column(
        Enum(
            FindingField,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=32,
            name="ck_finding_evidence_field_name",
        )
    )
    source_excerpt: Mapped[str] = mapped_column(Text)
    start_offset: Mapped[int]
    end_offset: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
