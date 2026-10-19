"""Сервис заказов. Статусы и переходы проверяются здесь, а не в клиентах."""

import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Ctx
from app.core.errors import AppError, Forbidden, NotFound
from app.core.security import now
from app.modules.audit import service as audit
from app.modules.billing.models import Discount, Payment
from app.modules.identity.models import Staff
from app.modules.menu.service import resolve_for_order
from app.modules.orders.models import (
    ITEM_CANCELLED,
    ITEM_COOKING,
    ITEM_QUEUED,
    ITEM_READY,
    ITEM_SERVED,
    ORDER_ACTIVE,
    ORDER_BILLED,
    ORDER_CANCELLED,
    ORDER_OPEN,
    ORDER_PAID,
    ORDER_PENDING,
    Order,
    OrderCounter,
    OrderItem,
    OrderItemModifier,
)
from app.modules.orders.schemas import (
    DiscountOut,
    OrderItemModifierOut,
    OrderItemOut,
    OrderOut,
    PaymentOut,
)
from app.modules.printing.service import create_job
from app.modules.realtime import service as rt
from app.modules.stations.models import Station
from app.modules.venues.models import Table, VenueSettings
from app.modules.venues.service import business_date

# Разрешённые переходы статуса позиции
ITEM_TRANSITIONS: dict[str, set[str]] = {
    ITEM_QUEUED: {ITEM_COOKING, ITEM_READY, ITEM_CANCELLED},
    ITEM_COOKING: {ITEM_READY, ITEM_QUEUED, ITEM_CANCELLED},
    ITEM_READY: {ITEM_SERVED, ITEM_COOKING, ITEM_CANCELLED},
    ITEM_SERVED: {ITEM_CANCELLED},
    ITEM_CANCELLED: set(),
}
KITCHEN_STATUSES = {ITEM_QUEUED, ITEM_COOKING, ITEM_READY}


# ---------- Чтение ----------


async def get_order(session: AsyncSession, order_id: uuid.UUID, *, lock: bool = False) -> Order:
    order = await session.get(Order, order_id, with_for_update=lock)
    if order is None:
        raise NotFound("order")
    return order


async def order_out(session: AsyncSession, order: Order) -> OrderOut:
    items = list((await session.scalars(
        select(OrderItem).where(OrderItem.order_id == order.id).order_by(OrderItem.created_at, OrderItem.id))).all())
    mods = list((await session.scalars(
        select(OrderItemModifier).where(OrderItemModifier.order_item_id.in_([i.id for i in items])))).all()) if items else []
    by_item: dict[uuid.UUID, list[OrderItemModifierOut]] = defaultdict(list)
    for m in mods:
        by_item[m.order_item_id].append(OrderItemModifierOut.model_validate(m))
    payments = (await session.scalars(select(Payment).where(Payment.order_id == order.id).order_by(Payment.created_at))).all()
    discounts = (await session.scalars(select(Discount).where(Discount.order_id == order.id))).all()
    out = OrderOut.model_validate(order)
    out.items = []
    for i in items:
        io = OrderItemOut.model_validate(i)
        io.modifiers = by_item.get(i.id, [])
        out.items.append(io)
    out.payments = [PaymentOut.model_validate(p) for p in payments]
    out.discounts = [DiscountOut.model_validate(d) for d in discounts]
    out.remaining = max(order.total - order.paid_total, 0)
    return out


def ensure_can_edit(ctx: Ctx, order: Order) -> None:
    if order.waiter_id not in (None, ctx.staff_id) and not ctx.has("orders.view_all"):
        raise Forbidden(reason="not_your_order")


def ensure_active(order: Order) -> None:
    if order.status not in ORDER_ACTIVE:
        raise AppError("order_not_editable", 409, {"status": order.status})


# ---------- Итоги ----------


async def recalc(session: AsyncSession, order: Order) -> None:
    items = (await session.scalars(
        select(OrderItem.total).where(OrderItem.order_id == order.id, OrderItem.status != ITEM_CANCELLED))).all()
    subtotal = sum(items)
    discount = 0
    for d in (await session.scalars(select(Discount).where(Discount.order_id == order.id))).all():
        discount += subtotal * d.value // 10000 if d.kind == "percent" else d.value
    discount = min(discount, subtotal)
    settings = await session.get(VenueSettings, order.venue_id)
    bp = settings.service_charge_bp if settings else 0
    service = (subtotal - discount) * bp // 10000
    order.subtotal, order.discount_total, order.service_charge = subtotal, discount, service
    order.total = subtotal - discount + service
    order.updated_at = now()


# ---------- Создание ----------


async def next_number(session: AsyncSession, venue_id: uuid.UUID, bdate) -> int:
    stmt = (
        pg_insert(OrderCounter)
        .values(venue_id=venue_id, business_date=bdate, last_number=1)
        .on_conflict_do_update(index_elements=["venue_id", "business_date"],
                               set_={"last_number": OrderCounter.last_number + 1})
        .returning(OrderCounter.last_number)
    )
    return int((await session.execute(stmt)).scalar_one())


async def current_shift_id(session: AsyncSession) -> uuid.UUID | None:
    from app.modules.shifts.models import Shift

    return await session.scalar(select(Shift.id).where(Shift.status == "open"))


def _channels(order: Order, station_ids: set[uuid.UUID] | None = None) -> list[str]:
    ch = [rt.ch_floor(order.venue_id), rt.ch_admin(order.venue_id)]
    if order.waiter_id:
        ch.append(rt.ch_user(order.waiter_id))
    ch += [rt.ch_station(s) for s in (station_ids or set()) if s]
    return ch


def brief(order: Order) -> dict:
    return {"id": order.id, "number": order.number, "table_id": order.table_id, "waiter_id": order.waiter_id,
            "status": order.status, "total": order.total, "paid_total": order.paid_total}


async def create_order(ctx: Ctx, data, *, source: str = "waiter") -> Order:
    s = ctx.session
    if data.table_id:
        table = await s.get(Table, data.table_id)
        if table is None or not table.is_active:
            raise NotFound("table")
    bdate = await business_date(s, ctx.venue_id)
    order = Order(
        id=data.id or uuid.uuid4(), venue_id=ctx.venue_id, business_date=bdate,
        number=await next_number(s, ctx.venue_id, bdate), table_id=data.table_id,
        waiter_id=ctx.staff_id if source != "site" else None, shift_id=await current_shift_id(s),
        status=ORDER_PENDING if source == "site" else ORDER_OPEN, source=source, guests=data.guests,
        comment=data.comment,
    )
    try:
        async with s.begin_nested():
            s.add(order)
            await s.flush()
    except IntegrityError as e:
        if "uq_orders_one_open_per_table" in str(e.orig):
            raise AppError("table_busy", 409, {"table_id": str(data.table_id)}) from e
        raise
    await audit.record(ctx, "order.created", "order", order.id, number=order.number, table_id=order.table_id)
    await rt.emit(s, ctx.venue_id, rt.ORDER_CREATED, _channels(order), brief(order))
    if data.items:
        await add_items(ctx, order, data.items)
    else:
        await recalc(s, order)
    await s.flush()
    return order


async def add_items(ctx: Ctx, order: Order, items_in) -> list[OrderItem]:
    s = ctx.session
    ensure_active(order)
    if order.status == ORDER_BILLED:
        order.status = ORDER_OPEN  # дозаказ после пречека — пречек нужно перепечатать
        order.billed_at = None
    created: list[OrderItem] = []
    mods_by_item: dict[uuid.UUID, list[OrderItemModifier]] = {}
    for it in items_in:
        menu_item, mods = await resolve_for_order(s, it.menu_item_id, it.modifier_ids)
        mods_total = sum(m.price for m in mods)
        oi = OrderItem(
            id=it.id or uuid.uuid4(), venue_id=ctx.venue_id, order_id=order.id, menu_item_id=menu_item.id,
            station_id=menu_item.station_id, name=menu_item.name, price=menu_item.price, quantity=it.quantity,
            modifiers_total=mods_total, total=(menu_item.price + mods_total) * it.quantity,
            status=ITEM_QUEUED,
            comment=it.comment, course=it.course, created_by=ctx.staff_id,
        )
        s.add(oi)
        created.append(oi)
        mods_by_item[oi.id] = [
            OrderItemModifier(venue_id=ctx.venue_id, order_item_id=oi.id, modifier_id=m.id, name=m.name, price=m.price)
            for m in mods
        ]
    await s.flush()
    for rows in mods_by_item.values():
        s.add_all(rows)
    await recalc(s, order)
    await s.flush()

    if order.status != ORDER_PENDING:
        await _send_to_stations(ctx, order, created, mods_by_item)
    await audit.record(ctx, "order.items_added", "order", order.id,
                       items=[{"id": i.id, "name": i.name, "qty": i.quantity} for i in created])
    return created


async def _send_to_stations(ctx: Ctx, order: Order, items: list[OrderItem], mods_by_item) -> None:
    s = ctx.session
    table = await s.get(Table, order.table_id) if order.table_id else None
    waiter = await _staff_name(s, order.waiter_id)
    by_station: dict[uuid.UUID | None, list[OrderItem]] = defaultdict(list)
    for i in items:
        by_station[i.station_id].append(i)
    wire = []
    for i in items:
        io = OrderItemOut.model_validate(i)
        io.modifiers = [OrderItemModifierOut.model_validate(m) for m in mods_by_item.get(i.id, [])]
        wire.append(io.model_dump(mode="json"))
    await rt.emit(s, ctx.venue_id, rt.ITEMS_ADDED, _channels(order, set(by_station)),
                  {"order": brief(order), "table_name": table.name if table else None, "items": wire})
    # Кухонные бегунки — на принтеры станций (печатает «Касса»)
    for station_id, st_items in by_station.items():
        station = await s.get(Station, station_id) if station_id else None
        if station and station.printer_id:
            await create_job(s, ctx.venue_id, "kitchen_ticket", {
                "station": station.name, "order_number": order.number,
                "table": table.name if table else None, "waiter": waiter, "created_at": now(),
                "items": [{"name": i.name, "qty": i.quantity, "comment": i.comment, "course": i.course,
                           "modifiers": [m.name for m in mods_by_item.get(i.id, [])]} for i in st_items],
            }, printer_id=station.printer_id)


async def _staff_name(session: AsyncSession, staff_id: uuid.UUID | None) -> str | None:
    if not staff_id:
        return None
    return await session.scalar(select(Staff.name).where(Staff.id == staff_id))


async def confirm_pending(ctx: Ctx, order: Order) -> Order:
    """Заказ с сайта подтверждает официант — он становится его заказом и уходит на кухню."""
    if order.status != ORDER_PENDING:
        raise AppError("invalid_transition", 409, {"from": order.status, "to": ORDER_OPEN})
    order.status = ORDER_OPEN
    order.waiter_id = ctx.staff_id
    items = list((await ctx.session.scalars(select(OrderItem).where(OrderItem.order_id == order.id))).all())
    mods = list((await ctx.session.scalars(
        select(OrderItemModifier).where(OrderItemModifier.order_item_id.in_([i.id for i in items])))).all()) if items else []
    by_item: dict[uuid.UUID, list[OrderItemModifier]] = defaultdict(list)
    for m in mods:
        by_item[m.order_item_id].append(m)
    await _send_to_stations(ctx, order, items, by_item)
    await rt.emit(ctx.session, ctx.venue_id, rt.ORDER_UPDATED, _channels(order), brief(order))
    await audit.record(ctx, "order.confirmed", "order", order.id)
    await ctx.session.flush()
    return order


# ---------- Статусы позиций ----------


async def change_item_status(ctx: Ctx, item_id: uuid.UUID, new_status: str, reason: str | None) -> OrderItem:
    s = ctx.session
    item = await s.get(OrderItem, item_id, with_for_update=True)
    if item is None:
        raise NotFound("order_item")
    order = await get_order(s, item.order_id, lock=True)
    old = item.status
    if new_status == old:
        return item
    if new_status not in ITEM_TRANSITIONS.get(old, set()):
        raise AppError("invalid_transition", 409, {"from": old, "to": new_status})
    if order.status == ORDER_PENDING:
        raise AppError("order_not_editable", 409, {"status": order.status})

    # Права: кухня меняет cooking/ready (только своих станций), официант — served; отмена — отдельно.
    if new_status == ITEM_CANCELLED:
        ensure_active(order)
        if old == ITEM_QUEUED and ctx.has("orders.create"):
            ensure_can_edit(ctx, order)
        else:
            ctx.require("orders.cancel")
            if not reason:
                raise AppError("validation_error", 422, {"field": "reason", "reason": "required"})
    elif new_status == ITEM_SERVED:
        if not (ctx.has("orders.create") or ctx.has("kitchen.work")):
            raise Forbidden(permission="orders.create")
    else:
        ctx.require("kitchen.work")
        if ctx.station_ids and item.station_id not in ctx.station_ids:
            raise Forbidden("station_forbidden", station_id=str(item.station_id))

    item.status = new_status
    ts = now()
    if new_status == ITEM_COOKING:
        item.cooking_at = ts
    elif new_status == ITEM_READY:
        item.ready_at = ts
    elif new_status == ITEM_SERVED:
        item.served_at = ts
    elif new_status == ITEM_QUEUED:
        item.cooking_at = None
    elif new_status == ITEM_CANCELLED:
        item.cancelled_at = ts
        item.cancel_reason = reason
        await recalc(s, order)
        await audit.record(ctx, "order_item.cancelled", "order_item", item.id, order_id=order.id, name=item.name,
                           from_status=old, reason=reason, amount=item.total)
    await s.flush()
    await rt.emit(s, ctx.venue_id, rt.ITEM_STATUS, _channels(order, {item.station_id} if item.station_id else set()), {
        "order_id": order.id, "order_number": order.number, "table_id": order.table_id, "item_id": item.id,
        "name": item.name, "quantity": item.quantity, "station_id": item.station_id, "from": old, "status": new_status,
        "order_total": order.total,
    })
    return item


# ---------- Перенос и отмена ----------


async def transfer(ctx: Ctx, order: Order, table_id: uuid.UUID | None, waiter_id: uuid.UUID | None) -> Order:
    s = ctx.session
    ensure_active(order)
    changes = {}
    if table_id and table_id != order.table_id:
        table = await s.get(Table, table_id)
        if table is None or not table.is_active:
            raise NotFound("table")
        changes["table_id"] = {"from": order.table_id, "to": table_id}
        order.table_id = table_id
    if waiter_id and waiter_id != order.waiter_id:
        waiter = await s.get(Staff, waiter_id)
        if waiter is None or not waiter.is_active:
            raise NotFound("staff")
        changes["waiter_id"] = {"from": order.waiter_id, "to": waiter_id}
        old_waiter = order.waiter_id
        order.waiter_id = waiter_id
        if old_waiter:
            await rt.emit(s, ctx.venue_id, rt.ORDER_UPDATED, [rt.ch_user(old_waiter)], brief(order))
    if not changes:
        return order
    order.updated_at = now()
    try:
        async with s.begin_nested():
            await s.flush()
    except IntegrityError as e:
        if "uq_orders_one_open_per_table" in str(e.orig):
            raise AppError("table_busy", 409, {"table_id": str(table_id)}) from e
        raise
    await audit.record(ctx, "order.transferred", "order", order.id, **changes)
    await rt.emit(s, ctx.venue_id, rt.ORDER_UPDATED, _channels(order), {**brief(order), "transfer": changes})
    return order


async def cancel_order(ctx: Ctx, order: Order, reason: str) -> Order:
    s = ctx.session
    ensure_active(order)
    has_payment = await s.scalar(
        select(Payment.id).where(Payment.order_id == order.id, Payment.refunded_at.is_(None)).limit(1))
    if has_payment:
        raise AppError("order_has_payments", 409)
    items = list((await s.scalars(
        select(OrderItem).where(OrderItem.order_id == order.id, OrderItem.status != ITEM_CANCELLED))).all())
    # Пустой заказ или заказ без начатых блюд официант может отменить сам; иначе — право orders.cancel.
    if any(i.status != ITEM_QUEUED for i in items) or not ctx.has("orders.create"):
        ctx.require("orders.cancel")
    else:
        ensure_can_edit(ctx, order)
    ts = now()
    for i in items:
        i.status, i.cancelled_at, i.cancel_reason = ITEM_CANCELLED, ts, reason
    order.status, order.cancelled_at, order.cancel_reason, order.closed_at = ORDER_CANCELLED, ts, reason, ts
    await recalc(s, order)
    await s.flush()
    await audit.record(ctx, "order.cancelled", "order", order.id, number=order.number, reason=reason,
                       amount=sum(i.total for i in items))
    await rt.emit(s, ctx.venue_id, rt.ORDER_CANCELLED, _channels(order, {i.station_id for i in items if i.station_id}),
                  {**brief(order), "reason": reason})
    return order


def mark_paid(order: Order) -> None:
    order.status = ORDER_PAID
    order.closed_at = now()
    order.updated_at = now()


__all__ = [
    "ITEM_TRANSITIONS", "KITCHEN_STATUSES", "ORDER_BILLED", "ORDER_OPEN", "ORDER_PAID", "add_items", "brief",
    "cancel_order", "change_item_status", "create_order", "ensure_active", "ensure_can_edit", "get_order",
    "mark_paid", "order_out", "recalc", "transfer",
]
