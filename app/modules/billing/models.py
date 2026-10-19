import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, Money, created_at, uuid_pk, venue_fk

PAYMENT_METHODS = ("cash", "card", "qr", "other")


class Payment(Base):
    """Несколько оплат на заказ (часть наличными, часть картой)."""

    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = venue_fk()
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id", ondelete="RESTRICT"), index=True)
    shift_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shifts.id", ondelete="RESTRICT"), index=True)
    method: Mapped[str] = mapped_column(String(20))
    amount: Mapped[int] = mapped_column(Money)  # зачтено в заказ
    tendered: Mapped[int | None] = mapped_column(Money)  # дал гость (для наличных)
    change: Mapped[int] = mapped_column(Money, default=0)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("staff.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at()
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refunded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("staff.id", ondelete="SET NULL"))


class Discount(Base):
    __tablename__ = "discounts"

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = venue_fk()
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # percent | amount
    value: Mapped[int] = mapped_column(Integer)  # bp для percent (1000 = 10%), тыйыны для amount
    reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("staff.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at()


class FiscalReceipt(Base):
    __tablename__ = "fiscal_receipts"

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = venue_fk()
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id", ondelete="RESTRICT"), index=True)
    kind: Mapped[str] = mapped_column(String(10), default="sale")  # sale | refund
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | sent | failed
    provider: Mapped[str | None] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    response: Mapped[dict | None] = mapped_column(JSONB)
    fiscal_number: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
