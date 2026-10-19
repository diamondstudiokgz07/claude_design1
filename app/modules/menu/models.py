import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, Money, created_at, uuid_pk, venue_fk


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = venue_fk()
    name: Mapped[str] = mapped_column(String(120))
    translations: Mapped[dict] = mapped_column(JSONB, default=dict)  # {"ky": "...", "ru": "..."}
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL"))
    default_station_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id", ondelete="SET NULL")
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()


class MenuItem(Base):
    __tablename__ = "menu_items"

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = venue_fk()
    category_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("categories.id", ondelete="RESTRICT"), index=True)
    station_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("stations.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    price: Mapped[int] = mapped_column(Money)
    weight_grams: Mapped[int | None] = mapped_column(Integer)
    cook_time_min: Mapped[int | None] = mapped_column(SmallInteger)
    photo_file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL"))
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)  # стоп-лист
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # показывать в меню
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()


class MenuItemTranslation(Base):
    __tablename__ = "menu_item_translations"

    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("menu_items.id", ondelete="CASCADE"), primary_key=True
    )
    lang: Mapped[str] = mapped_column(String(5), primary_key=True)
    venue_id: Mapped[uuid.UUID] = venue_fk()
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)


class ModifierGroup(Base):
    __tablename__ = "modifier_groups"

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = venue_fk()
    name: Mapped[str] = mapped_column(String(120))
    translations: Mapped[dict] = mapped_column(JSONB, default=dict)
    min_select: Mapped[int] = mapped_column(SmallInteger, default=0)
    max_select: Mapped[int] = mapped_column(SmallInteger, default=1)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()


class MenuItemModifierGroup(Base):
    __tablename__ = "menu_item_modifier_groups"

    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("menu_items.id", ondelete="CASCADE"), primary_key=True
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("modifier_groups.id", ondelete="CASCADE"), primary_key=True
    )
    venue_id: Mapped[uuid.UUID] = venue_fk()


class Modifier(Base):
    __tablename__ = "modifiers"

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = venue_fk()
    group_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("modifier_groups.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    translations: Mapped[dict] = mapped_column(JSONB, default=dict)
    price: Mapped[int] = mapped_column(Money, default=0)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
