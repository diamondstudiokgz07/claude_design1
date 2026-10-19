import asyncio
import contextlib
import time
import uuid

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.core.deps import Ctx, current_ctx, load_ctx
from app.core.errors import AppError
from app.core.security import decode_access_token
from app.db import get_sessionmaker
from app.modules.realtime import service as rt
from app.modules.realtime.hub import Connection, channels_for, hub

router = APIRouter(tags=["sync"])


class EventOut(BaseModel):
    seq: int
    type: str
    at: str
    data: dict


class EventsPage(BaseModel):
    events: list[EventOut]
    last_seq: int
    has_more: bool


@router.get("/events", response_model=EventsPage)
async def events_since(
    since: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=1000),
    stations: list[uuid.UUID] = Query(default=[]),
    ctx: Ctx = Depends(current_ctx),
):
    """Добор пропущенных событий после обрыва связи."""
    channels = await channels_for(ctx, stations or None)
    rows = await rt.events_since(ctx.session, ctx.venue_id, channels, since, limit + 1)
    has_more = len(rows) > limit
    rows = rows[:limit]
    return EventsPage(events=[EventOut(**rt.event_to_wire(e)) for e in rows],
                      last_seq=rows[-1].seq if rows else since, has_more=has_more)


@router.websocket("/ws")
async def websocket(ws: WebSocket, token: str, since: int | None = None, stations: str | None = None):
    """Один адрес /ws с токеном. ?since=seq — сразу дослать пропущенное; ?stations=id,id — станции кухни."""
    try:
        claims = decode_access_token(token)
        requested = [uuid.UUID(s) for s in stations.split(",") if s] if stations else None
        async with get_sessionmaker()() as session, session.begin():
            ctx = await load_ctx(session, token)
            channels = await channels_for(ctx, requested)
            venue_id = ctx.venue_id
    except (AppError, ValueError):
        await ws.close(code=4001)
        return

    await ws.accept()
    conn = Connection(ws=ws, venue_id=venue_id, channels=set(channels))
    hub.register(conn)  # сначала подписка, потом добор — чтобы не потерять события между ними
    try:
        await ws.send_json({"type": "HELLO", "channels": channels})
        if since is not None:
            async with get_sessionmaker()() as session, session.begin():
                from app.db import set_tenant

                await set_tenant(session, venue_id)
                cursor = since
                while True:
                    rows = await rt.events_since(session, venue_id, channels, cursor, 500)
                    for ev in rows:
                        await ws.send_json(rt.event_to_wire(ev))
                        conn.last_seq = ev.seq
                    if len(rows) < 500:
                        break
                    cursor = rows[-1].seq

        exp = float(claims["exp"])
        receiver = asyncio.create_task(_drain_incoming(ws))
        try:
            while True:
                if time.time() > exp:
                    await ws.close(code=4001)  # токен истёк: клиент обновит его и переподключится с since
                    return
                getter = asyncio.create_task(conn.queue.get())
                done, _ = await asyncio.wait({getter, receiver}, timeout=25, return_when=asyncio.FIRST_COMPLETED)
                if receiver in done:  # клиент закрыл соединение
                    getter.cancel()
                    return
                if getter not in done:
                    getter.cancel()
                    await ws.send_json({"type": "PING"})
                    continue
                event = getter.result()
                if event["seq"] <= conn.last_seq:
                    continue
                await ws.send_json(event)
                conn.last_seq = event["seq"]
                if event["type"] == rt.SESSION_REVOKED:
                    await ws.close(code=4003)
                    return
        finally:
            receiver.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await receiver
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.unregister(conn)


async def _drain_incoming(ws: WebSocket) -> None:
    """Клиент может слать PONG/пинги — читаем, чтобы заметить закрытие."""
    while True:
        await ws.receive_text()
