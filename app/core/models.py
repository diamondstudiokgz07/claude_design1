"""Системные таблицы, общие для модулей: файлы и ключи идемпотентности."""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, created_at, uuid_pk, venue_fk


class File(Base):
    __tablename__ = "files"

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = venue_fk()
    key: Mapped[str] = mapped_column(String(300), unique=True)
    thumb_key: Mapped[str | None] = mapped_column(String(300))
    content_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))  # photo | logo | export
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("staff.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at()


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    venue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("venues.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(12), default="processing")  # processing | done
    status_code: Mapped[int | None] = mapped_column(Integer)
    response: Mapped[dict | list | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at()
