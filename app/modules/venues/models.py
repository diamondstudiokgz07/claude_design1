import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, Money, created_at, uuid_pk, venue_fk


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = created_at()


class Venue(Base):
    __tablename__ = "venues"

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Bishkek")
    currency: Mapped[str] = mapped_column(String(3), default="KGS")
    languages: Mapped[list[str]] = mapped_column(ARRAY(String(5)), default=lambda: ["ky", "ru"])
    address: Mapped[str | None] = mapped_column(String(300))
    phone: Mapped[str | None] = mapped_column(String(40))
    working_hours: Mapped[dict] = mapped_column(JSONB, default=dict)
    logo_file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at()


class VenueSettings(Base):
    __tablename__ = "venue_settings"

    venue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("venues.id", ondelete="CASCADE"), primary_key=True
    )
    receipt_header: Mapped[str | None] = mapped_column(Text)
    receipt_footer: Mapped[str | None] = mapped_column(Text)
    legal_name: Mapped[str | None] = mapped_column(String(300))
    tax_id: Mapped[str | None] = mapped_column(String(32))  # ИНН
    service_charge_bp: Mapped[int] = mapped_column(Integer, default=0)  # обслуживание, базисные пункты (1000 = 10%)
    business_day_start_hour: Mapped[int] = mapped_column(SmallInteger, default=6)
    auto_close_shift: Mapped[bool] = mapped_column(Boolean, default=False)
    min_app_version: Mapped[str | None] = mapped_column(String(20))
    max_discount_bp: Mapped[int] = mapped_column(Integer, default=10000)
    default_opening_cash: Mapped[int] = mapped_column(Money, default=0)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)


class Hall(Base):
    __tablename__ = "halls"

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = venue_fk()
    name: Mapped[str] = mapped_column(String(100))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at()


class Table(Base):
    __tablename__ = "tables"
    __table_args__ = (UniqueConstraint("venue_id", "name", name="uq_tables_venue_name"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = venue_fk()
    hall_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("halls.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(50))
    seats: Mapped[int] = mapped_column(SmallInteger, default=4)
    qr_token: Mapped[str] = mapped_column(String(64), unique=True)  # случайный, меняется при перепечатке QR
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at()
