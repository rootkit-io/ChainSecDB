from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CHAR, DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RawDocument(Base):
    __tablename__ = "raw_documents"
    __table_args__ = (UniqueConstraint("content_hash", name="uq_raw_documents_content_hash"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    source_name: Mapped[str] = mapped_column(String(255))
    source_url: Mapped[str | None] = mapped_column(Text)
    document_type: Mapped[str] = mapped_column(String(255))
    raw_text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(CHAR(64))
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
