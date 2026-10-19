"""Публичный сервис модуля realtime: запись событий в outbox и вычисление каналов."""

import uuid
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.realtime.models import OutboxEvent

# Типы событий
ORDER_CREATED = "ORDER_CREATED"
ITEMS_ADDED = "ITEMS_ADDED"
ITEM_STATUS = "ITEM_STATUS"
ORDER_UPDATED = "ORDER_UPDATED"
ORDER_PAID = "ORDER_PAID"
ORDER_CANCELLED = "ORDER_CANCELLED"
STOP_LIST = "STOP_LIST"
MENU_CHANGED = "MENU_CHANGED"
STATIONS_CHANGED = "STATIONS_CHANGED"
SHIFT = "SHIFT"
SESSION_REVOKED = "SESSION_REVOKED"
PRINT_JOB = "PRINT_JOB"
WAITER_CALLED = "WAITER_CALLED"
SECURITY_ALERT = "SECURITY_ALERT"


def ch_floor(venue_id: uuid.UUID) -> str:
    return f"venue:{venue_id}:floor"


def ch_admin(venue_id: uuid.UUID) -> str:
    return f"venue:{venue_id}:admin"


def ch_user(staff_id: uuid.UUID) -> str:
    return f"user:{staff_id}"


def ch_station(station_id: uuid.UUID) -> str:
    return f"station:{station_id}"


def ch_device(device_id: uuid.UUID) -> str:
    return f"device:{device_id}"


def _lock_key(venue_id: uuid.UUID) -> int:
    # 63-битный ключ advisory-lock из UUID заведения
    return int.from_bytes(venue_id.bytes[:8], "big", signed=True)


async def emit(
    session: AsyncSession, venue_id: uuid.UUID, type_: str, channels: list[str], data: dict[str, Any]
) -> OutboxEvent:
    """Пишет событие в той же транзакции, что и изменение данных.

    Advisory-lock на заведение гарантирует, что события одного заведения фиксируются в порядке seq —
    клиент, добирающий события по since=seq, ничего не пропустит.
    """
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _lock_key(venue_id)})
    ev = OutboxEvent(venue_id=venue_id, type=type_, channels=sorted(set(channels)), data=_jsonable(data))
    session.add(ev)
    await session.flush()
    return ev


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def event_to_wire(ev: OutboxEvent) -> dict[str, Any]:
    return {"seq": ev.seq, "type": ev.type, "at": ev.created_at.isoformat(), "data": ev.data}


async def events_since(
    session: AsyncSession, venue_id: uuid.UUID, channels: list[str], since: int, limit: int
) -> list[OutboxEvent]:
    stmt = (
        select(OutboxEvent)
        .where(OutboxEvent.venue_id == venue_id, OutboxEvent.seq > since, OutboxEvent.channels.overlap(channels))
        .order_by(OutboxEvent.seq)
        .limit(limit)
    )
    return list((await session.scalars(stmt)).all())
