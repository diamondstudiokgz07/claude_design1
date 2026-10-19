"""WebSocket-шлюз: одно соединение на устройство; события приходят из Redis и раздаются по каналам."""

import asyncio
import contextlib
import json
import logging
import uuid
from dataclasses import dataclass, field

from fastapi import WebSocket
from sqlalchemy import select

from app.core.deps import Ctx
from app.core.redis import get_redis
from app.db import tenant_session
from app.modules.realtime import service as rt
from app.modules.realtime.models import OutboxEvent

log = logging.getLogger("dostuk.realtime")
REDIS_CHANNEL = "dostuk:events"


async def channels_for(ctx: Ctx, requested_stations: list[uuid.UUID] | None = None) -> list[str]:
    """Сервер сам подписывает соединение на каналы по роли."""
    from app.modules.stations.models import Station

    ch = {rt.ch_user(ctx.staff_id)}
    if ctx.device_id:
        ch.add(rt.ch_device(ctx.device_id))
    if ctx.has("orders.view"):
        ch.add(rt.ch_floor(ctx.venue_id))
    if ctx.has("printing.manage") or ctx.has("shifts.close") or ctx.has("staff.manage"):
        ch.add(rt.ch_admin(ctx.venue_id))
    if ctx.has("kitchen.work"):
        allowed = set(ctx.station_ids) if ctx.station_ids else set(
            (await ctx.session.scalars(select(Station.id).where(Station.archived_at.is_(None)))).all())
        chosen = allowed & set(requested_stations) if requested_stations else allowed
        ch |= {rt.ch_station(s) for s in chosen}
        # Кухне нужны стоп-лист и изменения станций
        ch.add(rt.ch_floor(ctx.venue_id))
    return sorted(ch)


@dataclass(eq=False)
class Connection:
    ws: WebSocket
    venue_id: uuid.UUID
    channels: set[str]
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=1000))
    last_seq: int = 0


class Hub:
    def __init__(self) -> None:
        self.connections: set[Connection] = set()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._listen())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    def register(self, conn: Connection) -> None:
        self.connections.add(conn)

    def unregister(self, conn: Connection) -> None:
        self.connections.discard(conn)

    def dispatch(self, message: dict) -> None:
        venue = message.get("venue_id")
        chans = set(message.get("channels", []))
        for conn in list(self.connections):
            if str(conn.venue_id) == venue and conn.channels & chans:
                try:
                    conn.queue.put_nowait(message["event"])
                except asyncio.QueueFull:
                    # Медленный клиент: закрываем — после переподключения он доберёт события по seq.
                    asyncio.create_task(conn.ws.close(code=4008))

    async def _listen(self) -> None:
        while True:
            try:
                pubsub = get_redis().pubsub()
                await pubsub.subscribe(REDIS_CHANNEL)
                async for msg in pubsub.listen():
                    if msg.get("type") == "message":
                        self.dispatch(json.loads(msg["data"]))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("redis subscriber failed, retrying")
                await asyncio.sleep(1)


hub = Hub()


async def publish_pending(batch: int = 500) -> int:
    """Фоновый публикатор: outbox → Redis. Событие помечается опубликованным только после отправки."""
    r = get_redis()
    async with tenant_session(None, bypass=True) as session:
        rows = list((await session.scalars(
            select(OutboxEvent).where(OutboxEvent.published_at.is_(None)).order_by(OutboxEvent.seq)
            .limit(batch).with_for_update(skip_locked=True))).all())
        if not rows:
            return 0
        pipe = r.pipeline()
        for ev in rows:
            pipe.publish(REDIS_CHANNEL, json.dumps(
                {"venue_id": str(ev.venue_id), "channels": ev.channels, "event": rt.event_to_wire(ev)}))
        await pipe.execute()
        from app.core.security import now

        ts = now()
        for ev in rows:
            ev.published_at = ts
        return len(rows)


async def publisher_loop(interval: float) -> None:
    while True:
        try:
            n = await publish_pending()
            if n == 0:
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("outbox publisher failed")
            await asyncio.sleep(2)
