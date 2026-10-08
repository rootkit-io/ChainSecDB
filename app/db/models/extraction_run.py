from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.extraction.transitions import ExtractionStatus


class ExtractionRun(Base):
    __tablename__ = "extraction_runs"
    __table_args__ = (
        UniqueConstraint("id", "raw_document_id", name="uq_extraction_runs_id_document"),
        CheckConstraint("provider ~ '[^[:space:]]'", name="ck_extraction_runs_provider"),
        CheckConstraint("model ~ '[^[:space:]]'", name="ck_extraction_runs_model"),
        CheckConstraint(
            "prompt_version ~ '[^[:space:]]'", name="ck_extraction_runs_prompt_version"
        ),
        CheckConstraint(
            "schema_version ~ '[^[:space:]]'", name="ck_extraction_runs_schema_version"
        ),
        CheckConstraint(
            "(status = 'PENDING' AND started_at IS NULL AND completed_at IS NULL) OR "
            "(status = 'RUNNING' AND started_at IS NOT NULL AND completed_at IS NULL) OR "
            "(status IN ('SUCCEEDED', 'FAILED') AND started_at IS NOT NULL "
            "AND completed_at IS NOT NULL)",
            name="ck_extraction_runs_lifecycle",
        ),
        CheckConstraint("completed_at >= started_at", name="ck_extraction_runs_timestamp_order"),
        CheckConstraint(
            "status = 'FAILED' OR (failure_code IS NULL AND failure_message IS NULL)",
            name="ck_extraction_runs_failure_state",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    raw_document_id: Mapped[UUID] = mapped_column(
        ForeignKey("raw_documents.id", ondelete="RESTRICT"), index=True
    )
    provider: Mapped[str] = mapped_column(String(255))
    model: Mapped[str] = mapped_column(String(255))
    prompt_version: Mapped[str] = mapped_column(String(255))
    schema_version: Mapped[str] = mapped_column(String(255))
    status: Mapped[ExtractionStatus] = mapped_column(
        Enum(
            ExtractionStatus,
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=16,
            name="ck_extraction_runs_status",
        ),
        default=ExtractionStatus.PENDING,
        server_default="PENDING",
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_message: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
