import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, MetaData, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import get_settings

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def uuid_pk() -> Mapped[uuid.UUID]:
    # Клиент может прислать свой UUID (создан офлайн) — сервер примет его.
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def venue_fk() -> Mapped[uuid.UUID]:
    from sqlalchemy import ForeignKey

    return mapped_column(UUID(as_uuid=True), ForeignKey("venues.id", ondelete="CASCADE"), index=True)


def created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


Money = BigInteger  # деньги — в тыйынах


_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine, _sessionmaker
    if _engine is None:
        s = get_settings()
        _engine = create_async_engine(s.database_url, pool_size=10, max_overflow=10, pool_pre_ping=True)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


async def set_tenant(session: AsyncSession, venue_id: uuid.UUID | None, *, bypass: bool = False) -> None:
    """Устанавливает контекст RLS на текущую транзакцию (SET LOCAL)."""
    await session.execute(
        text("SELECT set_config('app.venue_id', :v, true), set_config('app.bypass_rls', :b, true)"),
        {"v": str(venue_id) if venue_id else "", "b": "on" if bypass else "off"},
    )


@asynccontextmanager
async def tenant_session(venue_id: uuid.UUID | None, *, bypass: bool = False) -> AsyncIterator[AsyncSession]:
    """Сессия с открытой транзакцией и контекстом заведения. Коммит при успехе."""
    async with get_sessionmaker()() as session:
        async with session.begin():
            await set_tenant(session, venue_id, bypass=bypass)
            yield session
