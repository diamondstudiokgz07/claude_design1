"""Фоновый worker (arq): публикатор outbox, фискализация, автозакрытие смен, очистка.

Запуск: arq worker.main.WorkerSettings
"""

import asyncio
import contextlib
import logging
from datetime import timedelta

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import delete, or_, select

from app import models  # noqa: F401
from app.config import get_settings
from app.core.models import IdempotencyKey
from app.core.security import now
from app.db import dispose_engine, tenant_session
from app.modules.billing.models import FiscalReceipt
from app.modules.billing.service import send_fiscal
from app.modules.identity.models import RefreshToken
from app.modules.realtime.hub import publisher_loop
from app.modules.shifts import service as shifts
from app.modules.shifts.models import Shift
from app.modules.venues.models import VenueSettings
from app.modules.venues.service import business_date

log = logging.getLogger("dostuk.worker")


async def startup(ctx: dict) -> None:
    ctx["publisher"] = asyncio.create_task(publisher_loop(get_settings().outbox_poll_interval_sec))


async def shutdown(ctx: dict) -> None:
    task = ctx.get("publisher")
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await dispose_engine()


async def cleanup(ctx: dict) -> dict:
    """Старые ключи идемпотентности и истёкшие refresh-токены."""
    s = get_settings()
    async with tenant_session(None, bypass=True) as session:
        r1 = await session.execute(
            delete(IdempotencyKey).where(IdempotencyKey.created_at < now() - timedelta(hours=s.idempotency_ttl_hours)))
        r2 = await session.execute(
            delete(RefreshToken).where(or_(RefreshToken.expires_at < now() - timedelta(days=1),
                                           RefreshToken.revoked_at < now() - timedelta(days=7))))
    return {"idempotency_keys": r1.rowcount, "refresh_tokens": r2.rowcount}


async def fiscal_sender(ctx: dict) -> int:
    sent = 0
    async with tenant_session(None, bypass=True) as session:
        rows = (await session.scalars(
            select(FiscalReceipt).where(FiscalReceipt.status.in_(("pending", "failed")), FiscalReceipt.attempts < 10)
            .order_by(FiscalReceipt.created_at).limit(100).with_for_update(skip_locked=True))).all()
        for r in rows:
            try:
                await send_fiscal(session, r)
                sent += 1
            except Exception as e:  # noqa: BLE001
                r.status, r.error = "failed", str(e)[:500]
    return sent


async def auto_close_shifts(ctx: dict) -> int:
    """Закрывает смены прошлых бизнес-дней в заведениях с включённым автозакрытием."""
    closed = 0
    async with tenant_session(None, bypass=True) as session:
        rows = (await session.execute(
            select(Shift.venue_id, Shift.business_date)
            .join(VenueSettings, VenueSettings.venue_id == Shift.venue_id)
            .where(Shift.status == "open", VenueSettings.auto_close_shift.is_(True)))).all()
    for venue_id, bdate in rows:
        async with tenant_session(venue_id) as session:
            if bdate >= await business_date(session, venue_id):
                continue
            shift = await shifts.current(session)
            if shift is None:
                continue
            expected = (await shifts.report(session, shift))["expected_cash"]
            await shifts.close_shift(None, expected, "auto-close", session=session, venue_id=venue_id, force=True)
            closed += 1
    return closed


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    on_startup = startup
    on_shutdown = shutdown
    functions = [cleanup, fiscal_sender, auto_close_shifts]
    cron_jobs = [
        cron(fiscal_sender, second={0, 30}),
        cron(auto_close_shifts, minute=set(range(0, 60, 10))),
        cron(cleanup, minute={17}),
    ]
