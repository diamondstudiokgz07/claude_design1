import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, created_at, uuid_pk, venue_fk


class Printer(Base):
    __tablename__ = "printers"

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = venue_fk()
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(20), default="kitchen")  # receipt | kitchen
    connection: Mapped[str] = mapped_column(String(20), default="network")  # network | usb
    address: Mapped[str | None] = mapped_column(String(200))  # 192.168.1.50:9100 или имя USB-принтера
    paper_width: Mapped[int] = mapped_column(SmallInteger, default=80)
    is_default_receipt: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at()


class PrintJob(Base):
    """Backend создаёт задание, касса печатает ESC/POS и подтверждает."""

    __tablename__ = "print_jobs"
    __table_args__ = (Index("ix_print_jobs_venue_status", "venue_id", "status"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = venue_fk()
    printer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("printers.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(20))  # kitchen_ticket | precheck | receipt | x_report | z_report
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | done | failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    printed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
