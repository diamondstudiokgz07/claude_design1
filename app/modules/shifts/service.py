"""Смены, движение наличных, X/Z-отчёты."""

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Ctx
from app.core.errors import AppError
from app.core.security import now
from app.modules.audit import service as audit
from app.modules.billing.models import Discount, Payment
from app.modules.orders.models import (
    ITEM_CANCELLED,
    ORDER_ACTIVE,
    ORDER_CANCELLED,
    ORDER_PAID,
    Order,
    OrderItem,
)
from app.modules.printing.service import create_job
from app.modules.realtime import service as rt
from app.modules.shifts.models import CashMovement, Shift
from app.modules.venues.service import business_date, get_settings_row


async def current(session: AsyncSession, *, lock: bool = False) -> Shift | None:
    stmt = select(Shift).where(Shift.status == "open")
    if lock:
        stmt = stmt.with_for_update()
    return await session.scalar(stmt)


async def require_open(session: AsyncSession) -> Shift:
    shift = await current(session)
    if shift is None:
        raise AppError("shift_not_open", 409)
    return shift


async def open_shift(ctx: Ctx, opening_cash: int | None, comment: str | None) -> Shift:
    s = ctx.session
    if await current(s):
        raise AppError("shift_already_open", 409)
    settings = await get_settings_row(s, ctx.venue_id)
    number = (await s.scalar(select(func.coalesce(func.max(Shift.number), 0)))) + 1
    shift = Shift(
        venue_id=ctx.venue_id, number=number, business_date=await business_date(s, ctx.venue_id),
        opened_by=ctx.staff_id, opening_cash=settings.default_opening_cash if opening_cash is None else opening_cash,
        comment=comment,
    )
    from sqlalchemy.exc import IntegrityError

    try:
        async with s.begin_nested():
            s.add(shift)
            await s.flush()
    except IntegrityError as e:
        raise AppError("shift_already_open", 409) from e
    # Открытые заказы без смены привязываем к новой
    from sqlalchemy import update

    await s.execute(update(Order).where(Order.shift_id.is_(None), Order.status.in_(ORDER_ACTIVE)).values(shift_id=shift.id))
    await audit.record(ctx, "shift.opened", "shift", shift.id, number=number, opening_cash=shift.opening_cash)
    await rt.emit(s, ctx.venue_id, rt.SHIFT, [rt.ch_floor(ctx.venue_id), rt.ch_admin(ctx.venue_id)],
                  {"id": shift.id, "status": "open", "number": number})
    return shift


async def add_cash_movement(ctx: Ctx, kind: str, amount: int, comment: str | None) -> CashMovement:
    shift = await require_open(ctx.session)
    m = CashMovement(venue_id=ctx.venue_id, shift_id=shift.id, kind=kind, amount=amount, comment=comment,
                     created_by=ctx.staff_id)
    ctx.session.add(m)
    await ctx.session.flush()
    await audit.record(ctx, f"cash.{kind}", "cash_movement", m.id, amount=amount, comment=comment)
    return m


async def report(session: AsyncSession, shift: Shift) -> dict[str, Any]:
    """Данные X/Z-отчёта по смене."""
    by_method = dict((await session.execute(
        select(Payment.method, func.sum(Payment.amount)).where(Payment.shift_id == shift.id, Payment.refunded_at.is_(None))
        .group_by(Payment.method))).all())
    refunds = dict((await session.execute(
        select(Payment.method, func.sum(Payment.amount)).where(Payment.shift_id == shift.id, Payment.refunded_at.is_not(None))
        .group_by(Payment.method))).all())
    cash = dict((await session.execute(
        select(CashMovement.kind, func.sum(CashMovement.amount)).where(CashMovement.shift_id == shift.id)
        .group_by(CashMovement.kind))).all())
    paid_orders = await session.scalar(
        select(func.count()).select_from(Order).where(Order.shift_id == shift.id, Order.status == ORDER_PAID)) or 0
    cancelled_orders = await session.scalar(
        select(func.count()).select_from(Order).where(Order.shift_id == shift.id, Order.status == ORDER_CANCELLED)) or 0
    open_orders = await session.scalar(
        select(func.count()).select_from(Order).where(Order.shift_id == shift.id, Order.status.in_(ORDER_ACTIVE))) or 0
    discounts = await session.scalar(
        select(func.coalesce(func.sum(Order.discount_total), 0)).where(Order.shift_id == shift.id, Order.status == ORDER_PAID)) or 0
    service = await session.scalar(
        select(func.coalesce(func.sum(Order.service_charge), 0)).where(Order.shift_id == shift.id, Order.status == ORDER_PAID)) or 0
    cancelled_items_total = await session.scalar(
        select(func.coalesce(func.sum(OrderItem.total), 0)).join(Order, Order.id == OrderItem.order_id)
        .where(Order.shift_id == shift.id, OrderItem.status == ITEM_CANCELLED)) or 0
    discount_count = await session.scalar(
        select(func.count()).select_from(Discount).join(Order, Order.id == Discount.order_id).where(Order.shift_id == shift.id)) or 0

    revenue = sum(int(v) for v in by_method.values())
    cash_sales = int(by_method.get("cash", 0))
    expected_cash = (shift.opening_cash + cash_sales + int(cash.get("deposit", 0))
                     - int(cash.get("expense", 0)) - int(cash.get("collection", 0)))
    return {
        "shift_id": str(shift.id), "number": shift.number, "business_date": shift.business_date.isoformat(),
        "opened_at": shift.opened_at.isoformat(), "closed_at": shift.closed_at.isoformat() if shift.closed_at else None,
        "opening_cash": shift.opening_cash, "revenue": revenue,
        "by_method": {k: int(v) for k, v in by_method.items()},
        "refunds": {k: int(v) for k, v in refunds.items()},
        "cash_movements": {k: int(v) for k, v in cash.items()},
        "expected_cash": expected_cash, "counted_cash": shift.counted_cash,
        "cash_difference": (shift.counted_cash - expected_cash) if shift.counted_cash is not None else None,
        "orders_paid": paid_orders, "orders_cancelled": cancelled_orders, "orders_open": open_orders,
        "average_check": revenue // paid_orders if paid_orders else 0,
        "discounts_total": int(discounts), "discounts_count": discount_count, "service_charge_total": int(service),
        "cancelled_items_total": int(cancelled_items_total),
    }


async def x_report(ctx: Ctx, print_it: bool) -> dict[str, Any]:
    shift = await require_open(ctx.session)
    data = await report(ctx.session, shift)
    if print_it:
        await create_job(ctx.session, ctx.venue_id, "x_report", data)
    return data


async def close_shift(ctx: Ctx | None, counted_cash: int, comment: str | None, *, session: AsyncSession | None = None,
                      venue_id: uuid.UUID | None = None, force: bool = False) -> Shift:
    """Закрытие смены и Z-отчёт. ctx=None — автозакрытие из фоновой задачи."""
    s = ctx.session if ctx else session
    vid = ctx.venue_id if ctx else venue_id
    assert s is not None and vid is not None
    shift = await current(s, lock=True)
    if shift is None:
        raise AppError("shift_not_open", 409)
    open_orders = await s.scalar(
        select(func.count()).select_from(Order).where(Order.status.in_(ORDER_ACTIVE))) or 0
    if open_orders and not force:
        raise AppError("conflict", 409, {"reason": "open_orders", "count": open_orders})
    shift.counted_cash = counted_cash
    shift.closed_at = now()
    shift.closed_by = ctx.staff_id if ctx else None
    shift.status = "closed"
    if comment:
        shift.comment = comment
    data = await report(s, shift)
    shift.expected_cash = data["expected_cash"]
    shift.z_report = data
    await s.flush()
    await create_job(s, vid, "z_report", data)
    if ctx:
        await audit.record(ctx, "shift.closed", "shift", shift.id, counted_cash=counted_cash,
                           expected_cash=data["expected_cash"], difference=data["cash_difference"])
    await rt.emit(s, vid, rt.SHIFT, [rt.ch_floor(vid), rt.ch_admin(vid)],
                  {"id": shift.id, "status": "closed", "number": shift.number})
    return shift
