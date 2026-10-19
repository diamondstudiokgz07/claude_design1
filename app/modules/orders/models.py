import uuid
from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, Money, created_at, uuid_pk, venue_fk

# Статусы заказа. Переходы проверяются в orders.service.
ORDER_PENDING = "pending"  # ждёт подтверждения официанта (заказ с сайта)
ORDER_OPEN = "open"
ORDER_BILLED = "billed"  # пречек напечатан, ждём оплату
ORDER_PAID = "paid"
ORDER_CANCELLED = "cancelled"
ORDER_ACTIVE = (ORDER_PENDING, ORDER_OPEN, ORDER_BILLED)

# Статусы позиции
ITEM_QUEUED = "queued"  # отправлено на станцию
ITEM_COOKING = "cooking"
ITEM_READY = "ready"
ITEM_SERVED = "served"
ITEM_CANCELLED = "cancelled"


class OrderCounter(Base):
    """Счётчик номеров заказов на бизнес-день (атомарный UPSERT ... RETURNING)."""

    __tablename__ = "order_counters"

    venue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("venues.id", ondelete="CASCADE"), primary_key=True
    )
    business_date: Mapped[date] = mapped_column(Date, primary_key=True)
    last_number: Mapped[int] = mapped_column(Integer, default=0)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("venue_id", "business_date", "number", name="uq_orders_venue_date_number"),
        # Один открытый заказ на стол
        Index(
            "uq_orders_one_open_per_table",
            "table_id",
            unique=True,
            postgresql_where=text("status IN ('pending','open','billed') AND table_id IS NOT NULL"),
        ),
        Index("ix_orders_venue_status", "venue_id", "status"),
        Index("ix_orders_venue_opened", "venue_id", "opened_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = venue_fk()
    business_date: Mapped[date] = mapped_column(Date)
    number: Mapped[int] = mapped_column(Integer)
    table_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tables.id", ondelete="RESTRICT"), index=True)
    waiter_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("staff.id", ondelete="RESTRICT"), index=True)
    shift_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("shifts.id", ondelete="RESTRICT"), index=True)
    status: Mapped[str] = mapped_column(String(20), default=ORDER_OPEN)
    source: Mapped[str] = mapped_column(String(20), default="waiter")  # waiter | pos | site
    guests: Mapped[int] = mapped_column(SmallInteger, default=1)
    comment: Mapped[str | None] = mapped_column(Text)
    subtotal: Mapped[int] = mapped_column(Money, default=0)
    discount_total: Mapped[int] = mapped_column(Money, default=0)
    service_charge: Mapped[int] = mapped_column(Money, default=0)
    total: Mapped[int] = mapped_column(Money, default=0)
    paid_total: Mapped[int] = mapped_column(Money, default=0)
    opened_at: Mapped[datetime] = created_at()
    billed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = created_at()


class OrderItem(Base):
    """Позиция копирует цену, название и станцию на момент заказа."""

    __tablename__ = "order_items"
    __table_args__ = (Index("ix_order_items_station_status", "venue_id", "station_id", "status"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = venue_fk()
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    menu_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("menu_items.id", ondelete="SET NULL")
    )
    station_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("stations.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(160))
    price: Mapped[int] = mapped_column(Money)
    quantity: Mapped[int] = mapped_column(SmallInteger, default=1)
    modifiers_total: Mapped[int] = mapped_column(Money, default=0)  # сумма модификаторов на 1 шт.
    total: Mapped[int] = mapped_column(Money)  # (price + modifiers_total) * quantity
    status: Mapped[str] = mapped_column(String(20), default=ITEM_QUEUED)
    comment: Mapped[str | None] = mapped_column(Text)
    course: Mapped[int] = mapped_column(SmallInteger, default=1)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("staff.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at()
    cooking_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    served_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)


class OrderItemModifier(Base):
    __tablename__ = "order_item_modifiers"

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = venue_fk()
    order_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("order_items.id", ondelete="CASCADE"), index=True
    )
    modifier_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("modifiers.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(120))
    price: Mapped[int] = mapped_column(Money, default=0)
