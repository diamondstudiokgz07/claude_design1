import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, Money, created_at, uuid_pk, venue_fk

CASH_KINDS = ("expense", "deposit", "collection")  # расход, внесение, инкассация


class Shift(Base):
    __tablename__ = "shifts"
    __table_args__ = (
        Index("uq_shifts_one_open_per_venue", "venue_id", unique=True, postgresql_where=text("status = 'open'")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = venue_fk()
    number: Mapped[int] = mapped_column(Integer)
    business_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), default="open")  # open | closed
    opened_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("staff.id", ondelete="SET NULL"))
    closed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("staff.id", ondelete="SET NULL"))
    opened_at: Mapped[datetime] = created_at()
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opening_cash: Mapped[int] = mapped_column(Money, default=0)
    expected_cash: Mapped[int | None] = mapped_column(Money)
    counted_cash: Mapped[int | None] = mapped_column(Money)
    z_report: Mapped[dict | None] = mapped_column(JSONB)
    comment: Mapped[str | None] = mapped_column(Text)


class CashMovement(Base):
    """Расходы, внесения и инкассации — одна таблица движения денег."""

    __tablename__ = "cash_movements"

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = venue_fk()
    shift_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shifts.id", ondelete="RESTRICT"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    amount: Mapped[int] = mapped_column(Money)  # всегда > 0; знак определяется видом
    comment: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("staff.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at()
