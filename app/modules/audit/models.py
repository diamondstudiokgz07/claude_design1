import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Index, String
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, created_at, venue_fk


class AuditLog(Base):
    """Неизменяемый журнал: UPDATE/DELETE запрещены триггером в БД."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_venue_created", "venue_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    venue_id: Mapped[uuid.UUID] = venue_fk()
    staff_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    device_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(60), index=True)
    entity: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    ip: Mapped[str | None] = mapped_column(INET)
    created_at: Mapped[datetime] = created_at()
