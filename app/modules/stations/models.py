import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, created_at, uuid_pk, venue_fk


class Station(Base):
    """Кухонная станция (цех). Не удаляется физически — архивируется, чтобы не терять отчёты."""

    __tablename__ = "stations"

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = venue_fk()
    name: Mapped[str] = mapped_column(String(80))
    color: Mapped[str] = mapped_column(String(9), default="#FF7A00")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    printer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("printers.id", ondelete="SET NULL"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # false = временно выключена (блюда в стоп-листе)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
