"""Публичное API для сайта-меню: только чтение, без авторизации, с кэшем и лимитами."""

import json
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import storage
from app.core.deps import client_ip, db_system
from app.core.errors import NotFound
from app.core.models import File
from app.core.redis import get_redis, rate_limit
from app.db import set_tenant
from app.modules.menu.models import Category, MenuItem, MenuItemTranslation
from app.modules.realtime import service as rt
from app.modules.stations.models import Station
from app.modules.venues.models import Table, Venue

router = APIRouter(prefix="/public", tags=["public"])


class CallWaiterIn(BaseModel):
    t: str = Field(min_length=8, max_length=64)  # токен стола из QR
    reason: Literal["call", "bill"] = "call"


async def _venue_by_slug(session: AsyncSession, slug: str) -> Venue:
    venue = await session.scalar(select(Venue).where(Venue.slug == slug, Venue.is_active.is_(True)))
    if venue is None:
        raise NotFound("venue")
    return venue


async def _guard(request: Request) -> None:
    await rate_limit(f"public:{client_ip(request)}", get_settings().public_rate_limit_per_min, 60)


async def build_menu(session: AsyncSession, venue: Venue, lang: str) -> dict:
    await set_tenant(session, venue.id)
    cats = (await session.scalars(
        select(Category).where(Category.archived_at.is_(None), Category.is_active.is_(True))
        .order_by(Category.sort_order, Category.name))).all()
    items = (await session.scalars(
        select(MenuItem).where(MenuItem.archived_at.is_(None), MenuItem.is_active.is_(True))
        .order_by(MenuItem.sort_order, MenuItem.name))).all()
    trs = {(t.item_id): t for t in (await session.scalars(
        select(MenuItemTranslation).where(MenuItemTranslation.lang == lang))).all()}
    active_stations = set((await session.scalars(
        select(Station.id).where(Station.is_active.is_(True), Station.archived_at.is_(None)))).all())
    file_ids = [i.photo_file_id for i in items if i.photo_file_id]
    files = {f.id: f for f in (await session.scalars(select(File).where(File.id.in_(file_ids)))).all()} if file_ids else {}

    def item_json(i: MenuItem) -> dict:
        tr = trs.get(i.id)
        f = files.get(i.photo_file_id) if i.photo_file_id else None
        return {
            "id": str(i.id), "name": tr.name if tr else i.name,
            "description": (tr.description if tr and tr.description else i.description),
            "price": i.price, "weight_grams": i.weight_grams,
            "photo_url": storage.public_url(f.key) if f else None,
            "photo_thumb_url": storage.public_url(f.thumb_key) if f else None,
            "available": i.is_available and i.station_id in active_stations,
        }

    return {
        "venue": {"name": venue.name, "slug": venue.slug, "address": venue.address, "phone": venue.phone,
                  "working_hours": venue.working_hours, "languages": venue.languages, "currency": venue.currency},
        "lang": lang,
        "categories": [
            {"id": str(c.id), "name": (c.translations or {}).get(lang) or c.name,
             "items": [item_json(i) for i in items if i.category_id == c.id]}
            for c in cats
        ],
    }


@router.get("/{venue_slug}/menu")
async def public_menu(
    venue_slug: str, request: Request, response: Response, lang: Literal["ky", "ru"] = "ru",
    session: AsyncSession = Depends(db_system),
):
    await _guard(request)
    venue = await _venue_by_slug(session, venue_slug)
    key = f"public:menu:{venue.id}:{lang}"
    r = get_redis()
    cached = await r.get(key)
    if cached:
        data = json.loads(cached)
    else:
        data = await build_menu(session, venue, lang)
        await r.set(key, json.dumps(data), ex=get_settings().public_cache_ttl_sec)
    response.headers["Cache-Control"] = f"public, max-age={get_settings().public_cache_ttl_sec}"
    return data


@router.get("/{venue_slug}/table/{token}")
async def public_table(venue_slug: str, token: str, request: Request, session: AsyncSession = Depends(db_system)):
    await _guard(request)
    venue = await _venue_by_slug(session, venue_slug)
    table = await session.scalar(select(Table).where(Table.venue_id == venue.id, Table.qr_token == token,
                                                     Table.is_active.is_(True)))
    if table is None:
        raise NotFound("table")
    return {"table": {"id": str(table.id), "name": table.name}, "venue": venue.slug}


@router.post("/{venue_slug}/call-waiter", status_code=202)
async def call_waiter(venue_slug: str, body: CallWaiterIn, request: Request, session: AsyncSession = Depends(db_system)):
    """Кнопка «Позвать официанта» (вход по QR стола)."""
    await _guard(request)
    venue = await _venue_by_slug(session, venue_slug)
    table = await session.scalar(select(Table).where(Table.venue_id == venue.id, Table.qr_token == body.t,
                                                     Table.is_active.is_(True)))
    if table is None:
        raise NotFound("table")
    await rate_limit(f"call:{table.id}", 3, 60)
    from app.modules.orders.models import ORDER_ACTIVE, Order

    waiter_id: uuid.UUID | None = await session.scalar(
        select(Order.waiter_id).where(Order.table_id == table.id, Order.status.in_(ORDER_ACTIVE)))
    channels = [rt.ch_floor(venue.id)] + ([rt.ch_user(waiter_id)] if waiter_id else [])
    await rt.emit(session, venue.id, rt.WAITER_CALLED, channels,
                  {"table_id": table.id, "table_name": table.name, "reason": body.reason, "waiter_id": waiter_id})
    return {"ok": True}
