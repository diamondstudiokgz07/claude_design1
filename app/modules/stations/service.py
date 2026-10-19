"""Станции (цеха) — записи в БД, которыми управляет админ. Все операции — в одной транзакции + журнал."""

import uuid

from sqlalchemy import func, select, update

from app.core.deps import Ctx
from app.core.errors import AppError, NotFound
from app.core.security import now
from app.modules.audit import service as audit
from app.modules.identity.models import StaffStation
from app.modules.menu.models import Category, MenuItem
from app.modules.menu.service import invalidate_public_cache
from app.modules.orders.models import ITEM_COOKING, ITEM_QUEUED, ITEM_READY, OrderItem
from app.modules.printing.models import Printer
from app.modules.realtime import service as rt
from app.modules.stations.models import Station
from app.modules.stations.schemas import StationOut

OPEN_ITEM_STATUSES = (ITEM_QUEUED, ITEM_COOKING, ITEM_READY)


async def _get_live(ctx: Ctx, station_id: uuid.UUID) -> Station:
    st = await ctx.session.get(Station, station_id)
    if st is None or st.archived_at is not None:
        raise NotFound("station")
    return st


async def _check_printer(ctx: Ctx, printer_id: uuid.UUID | None) -> None:
    if printer_id and await ctx.session.get(Printer, printer_id) is None:
        raise NotFound("printer")


async def stations_out(ctx: Ctx, stations: list[Station]) -> list[StationOut]:
    ids = [s.id for s in stations]
    counts = dict((await ctx.session.execute(
        select(MenuItem.station_id, func.count()).where(MenuItem.station_id.in_(ids), MenuItem.archived_at.is_(None))
        .group_by(MenuItem.station_id)
    )).all()) if ids else {}
    out = []
    for s in stations:
        o = StationOut.model_validate(s)
        o.items_count = counts.get(s.id, 0)
        out.append(o)
    return out


async def _changed(ctx: Ctx, op: str, **data) -> None:
    await rt.emit(
        ctx.session, ctx.venue_id, rt.STATIONS_CHANGED,
        [rt.ch_floor(ctx.venue_id), rt.ch_admin(ctx.venue_id)] + [rt.ch_station(s) for s in data.get("station_ids", [])],
        {"op": op, **data},
    )


async def create(ctx: Ctx, data) -> Station:
    await _check_printer(ctx, data.printer_id)
    st = Station(id=data.id or uuid.uuid4(), venue_id=ctx.venue_id, name=data.name, color=data.color,
                 sort_order=data.sort_order, printer_id=data.printer_id)
    ctx.session.add(st)
    await ctx.session.flush()
    await audit.record(ctx, "station.created", "station", st.id, name=st.name)
    await _changed(ctx, "created", station_ids=[st.id])
    return st


async def update_station(ctx: Ctx, station_id: uuid.UUID, data) -> Station:
    st = await _get_live(ctx, station_id)
    changes = data.model_dump(exclude_unset=True)
    if "printer_id" in changes:
        await _check_printer(ctx, changes["printer_id"])
    was_active = st.is_active
    for k, v in changes.items():
        setattr(st, k, v)
    await ctx.session.flush()
    await audit.record(ctx, "station.updated", "station", station_id, **changes)
    await _changed(ctx, "updated", station_ids=[station_id])
    if "is_active" in changes and changes["is_active"] != was_active:
        # Выключение станции (например, гриль зимой): её блюда автоматически в стоп-листе.
        items = list((await ctx.session.scalars(
            select(MenuItem).where(MenuItem.station_id == station_id, MenuItem.archived_at.is_(None)))).all())
        await rt.emit(
            ctx.session, ctx.venue_id, rt.STOP_LIST,
            [rt.ch_floor(ctx.venue_id), rt.ch_admin(ctx.venue_id), rt.ch_station(station_id)],
            {"station_id": station_id, "station_active": st.is_active,
             "items": [{"id": i.id, "is_available": i.is_available and st.is_active} for i in items]},
        )
        await invalidate_public_cache(ctx.venue_id)
    return st


async def _move(
    ctx: Ctx, src: uuid.UUID, dst: uuid.UUID, *, item_ids: list[uuid.UUID] | None = None,
    category_ids: list[uuid.UUID] | None = None, move_open_items: bool = True,
) -> tuple[int, int, int]:
    """Переносит блюда (все или выбранные), станции по умолчанию у категорий и незакрытые позиции."""
    item_cond = [MenuItem.station_id == src]
    if item_ids is not None or category_ids is not None:
        sel = []
        if item_ids:
            sel.append(MenuItem.id.in_(item_ids))
        if category_ids:
            sel.append(MenuItem.category_id.in_(category_ids))
        if not sel:
            return 0, 0, 0
        from sqlalchemy import or_

        item_cond.append(or_(*sel))
    moved_ids = list((await ctx.session.scalars(select(MenuItem.id).where(*item_cond))).all())
    if moved_ids:
        await ctx.session.execute(update(MenuItem).where(MenuItem.id.in_(moved_ids)).values(station_id=dst))

    cat_cond = [Category.default_station_id == src]
    if category_ids is not None or item_ids is not None:
        cat_cond.append(Category.id.in_(category_ids or []))
    res = await ctx.session.execute(update(Category).where(*cat_cond).values(default_station_id=dst))
    moved_cats = res.rowcount or 0

    moved_open = 0
    if move_open_items:
        oi_cond = [OrderItem.station_id == src, OrderItem.status.in_(OPEN_ITEM_STATUSES)]
        if item_ids is not None or category_ids is not None:
            oi_cond.append(OrderItem.menu_item_id.in_(moved_ids))
        res = await ctx.session.execute(update(OrderItem).where(*oi_cond).values(station_id=dst))
        moved_open = res.rowcount or 0
    return len(moved_ids), moved_open, moved_cats


async def split(ctx: Ctx, station_id: uuid.UUID, data) -> tuple[Station, int, int, int]:
    """Разделить: из «Горячее» создаётся «Супы»; переезжают отмеченные блюда и категории."""
    src = await _get_live(ctx, station_id)
    new = await create(ctx, data.new_station)
    if data.category_ids:
        found = set((await ctx.session.scalars(select(Category.id).where(Category.id.in_(data.category_ids)))).all())
        if missing := set(data.category_ids) - found:
            raise NotFound("category", ids=[str(m) for m in missing])
    moved, moved_open, moved_cats = await _move(
        ctx, src.id, new.id, item_ids=data.item_ids, category_ids=data.category_ids, move_open_items=data.move_open_items
    )
    await audit.record(ctx, "station.split", "station", src.id, new_station_id=new.id, item_ids=data.item_ids,
                       category_ids=data.category_ids, moved_items=moved, moved_open=moved_open)
    await _changed(ctx, "split", station_ids=[src.id, new.id])
    return new, moved, moved_open, moved_cats


async def merge(ctx: Ctx, station_id: uuid.UUID, target_id: uuid.UUID) -> tuple[Station, int, int, int]:
    """Объединить: все блюда станции A переносятся в B, A архивируется."""
    if station_id == target_id:
        raise AppError("validation_error", 422, {"field": "target_id"})
    src = await _get_live(ctx, station_id)
    dst = await _get_live(ctx, target_id)
    moved, moved_open, moved_cats = await _move(ctx, src.id, dst.id)
    await _rebind_staff(ctx, src.id, dst.id)
    src.archived_at = now()
    src.is_active = False
    await audit.record(ctx, "station.merged", "station", src.id, target_id=dst.id, moved_items=moved, moved_open=moved_open)
    await _changed(ctx, "merged", station_ids=[src.id, dst.id], archived_id=src.id, target_id=dst.id)
    return dst, moved, moved_open, moved_cats


async def delete(ctx: Ctx, station_id: uuid.UUID, move_to: uuid.UUID | None) -> tuple[Station, int, int, int]:
    """Удалить можно, только указав, куда передать блюда и незакрытые позиции. Мягкое удаление."""
    src = await _get_live(ctx, station_id)
    has_items = await ctx.session.scalar(
        select(MenuItem.id).where(MenuItem.station_id == station_id, MenuItem.archived_at.is_(None)).limit(1))
    has_open = await ctx.session.scalar(
        select(OrderItem.id).where(OrderItem.station_id == station_id, OrderItem.status.in_(OPEN_ITEM_STATUSES)).limit(1))
    moved = moved_open = moved_cats = 0
    if move_to is None:
        if has_items or has_open:
            raise AppError("station_in_use", 409, {"has_items": bool(has_items), "has_open_items": bool(has_open)})
    else:
        if move_to == station_id:
            raise AppError("validation_error", 422, {"field": "move_to"})
        await _get_live(ctx, move_to)
        moved, moved_open, moved_cats = await _move(ctx, src.id, move_to)
        await _rebind_staff(ctx, src.id, move_to)
    # Архивные блюда тоже ссылаются на станцию — FK остаётся валидным, т.к. станция не удаляется физически.
    await ctx.session.execute(update(Category).where(Category.default_station_id == src.id).values(default_station_id=move_to))
    src.archived_at = now()
    src.is_active = False
    await audit.record(ctx, "station.deleted", "station", src.id, move_to=move_to, moved_items=moved, moved_open=moved_open)
    await _changed(ctx, "deleted", station_ids=[src.id] + ([move_to] if move_to else []), archived_id=src.id,
                   target_id=move_to)
    return src, moved, moved_open, moved_cats


async def _rebind_staff(ctx: Ctx, src: uuid.UUID, dst: uuid.UUID) -> None:
    """Повара, привязанные к архивируемой станции, получают станцию-получателя."""
    staff_ids = list((await ctx.session.scalars(select(StaffStation.staff_id).where(StaffStation.station_id == src))).all())
    already = set((await ctx.session.scalars(
        select(StaffStation.staff_id).where(StaffStation.station_id == dst, StaffStation.staff_id.in_(staff_ids)))).all()) if staff_ids else set()
    for sid in staff_ids:
        if sid not in already:
            ctx.session.add(StaffStation(staff_id=sid, station_id=dst, venue_id=ctx.venue_id))
    await ctx.session.execute(StaffStation.__table__.delete().where(StaffStation.station_id == src))
    await ctx.session.flush()
