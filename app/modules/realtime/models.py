import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, created_at, venue_fk


class OutboxEvent(Base):
    """seq — сквозной номер события; клиент добирает пропущенное через GET /events?since=seq."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        Index("ix_outbox_unpublished", "seq", postgresql_where=text("published_at IS NULL")),
        Index("ix_outbox_venue_seq", "venue_id", "seq"),
    )

    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    venue_id: Mapped[uuid.UUID] = venue_fk()
    type: Mapped[str] = mapped_column(String(40))
    channels: Mapped[list[str]] = mapped_column(ARRAY(String(80)))
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = created_at()
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
