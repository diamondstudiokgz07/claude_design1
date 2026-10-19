"""Счета, оплаты (в т.ч. разделённые), скидки, фискализация."""

import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Ctx
from app.core.errors import AppError, NotFound
from app.core.security import now
from app.modules.audit import service as audit
from app.modules.billing.models import Discount, FiscalReceipt, Payment
from app.modules.orders import service as orders
from app.modules.orders.models import (
    ITEM_CANCELLED,
    ORDER_BILLED,
    ORDER_OPEN,
    ORDER_PAID,
    Order,
    OrderItem,
)
from app.modules.printing.service import create_job
from app.modules.realtime import service as rt
from app.modules.shifts.service import require_open
from app.modules.venues.models import Table, Venue
from app.modules.venues.service import get_settings_row


async def receipt_payload(session: AsyncSession, order: Order, kind: str) -> dict[str, Any]:
    venue = await session.get(Venue, order.venue_id)
    settings = await get_settings_row(session, order.venue_id)
    table = await session.get(Table, order.table_id) if order.table_id else None
    items = (await session.scalars(
        select(OrderItem).where(OrderItem.order_id == order.id, OrderItem.status != ITEM_CANCELLED)
        .order_by(OrderItem.created_at))).all()
    payments = (await session.scalars(
        select(Payment).where(Payment.order_id == order.id, Payment.refunded_at.is_(None)))).all()
    return {
        "kind": kind, "venue": venue.name if venue else None, "address": venue.address if venue else None,
        "legal_name": settings.legal_name, "tax_id": settings.tax_id,
        "header": settings.receipt_header, "footer": settings.receipt_footer,
        "order_number": order.number, "business_date": order.business_date.isoformat(),
        "table": table.name if table else None, "guests": order.guests,
        "items": [{"name": i.name, "qty": i.quantity, "price": i.price + i.modifiers_total, "total": i.total} for i in items],
        "subtotal": order.subtotal, "discount": order.discount_total, "service_charge": order.service_charge,
        "total": order.total, "payments": [{"method": p.method, "amount": p.amount, "tendered": p.tendered,
                                            "change": p.change} for p in payments],
        "printed_at": now(),
    }


async def bill(ctx: Ctx, order_id: uuid.UUID, print_it: bool = True) -> Order:
    """Пречек: заказ переходит в billed, печатается счёт для гостя."""
    order = await orders.get_order(ctx.session, order_id, lock=True)
    orders.ensure_active(order)
    if order.status not in (ORDER_OPEN, ORDER_BILLED):
        raise AppError("invalid_transition", 409, {"from": order.status, "to": ORDER_BILLED})
    await orders.recalc(ctx.session, order)
    order.status = ORDER_BILLED
    order.billed_at = now()
    await ctx.session.flush()
    if print_it:
        await create_job(ctx.session, ctx.venue_id, "precheck", await receipt_payload(ctx.session, order, "precheck"))
    await audit.record(ctx, "order.billed", "order", order.id, total=order.total)
    await rt.emit(ctx.session, ctx.venue_id, rt.ORDER_UPDATED, orders._channels(order), orders.brief(order))
    return order


async def add_discount(ctx: Ctx, order_id: uuid.UUID, kind: str, value: int, reason: str | None) -> Order:
    s = ctx.session
    order = await orders.get_order(s, order_id, lock=True)
    orders.ensure_active(order)
    if order.paid_total > 0:
        raise AppError("order_has_payments", 409)
    settings = await get_settings_row(s, ctx.venue_id)
    if kind == "percent" and value > settings.max_discount_bp:
        raise AppError("validation_error", 422, {"field": "value", "max_bp": settings.max_discount_bp})
    # Одна действующая скидка на заказ: новая заменяет прежнюю (история — в журнале)
    await s.execute(delete(Discount).where(Discount.order_id == order.id))
    if value > 0:
        s.add(Discount(venue_id=ctx.venue_id, order_id=order.id, kind=kind, value=value, reason=reason,
                       created_by=ctx.staff_id))
    await s.flush()
    before = order.total
    await orders.recalc(s, order)
    await s.flush()
    await audit.record(ctx, "order.discount", "order", order.id, kind=kind, value=value, reason=reason,
                       total_before=before, total_after=order.total)
    await rt.emit(s, ctx.venue_id, rt.ORDER_UPDATED, orders._channels(order), orders.brief(order))
    return order


async def add_payment(
    ctx: Ctx, order_id: uuid.UUID, method: str, amount: int, tendered: int | None, payment_id: uuid.UUID | None
) -> tuple[Order, Payment]:
    s = ctx.session
    shift = await require_open(s)
    order = await orders.get_order(s, order_id, lock=True)  # блокировка сериализует параллельные оплаты
    if order.status not in (ORDER_OPEN, ORDER_BILLED):
        raise AppError("order_not_editable", 409, {"status": order.status})
    await orders.recalc(s, order)
    remaining = order.total - order.paid_total
    if amount > remaining:
        raise AppError("overpayment", 409, {"remaining": remaining})
    change = 0
    if method == "cash":
        if tendered is not None:
            if tendered < amount:
                raise AppError("validation_error", 422, {"field": "tendered"})
            change = tendered - amount
    else:
        tendered = None
    p = Payment(id=payment_id or uuid.uuid4(), venue_id=ctx.venue_id, order_id=order.id, shift_id=shift.id,
                method=method, amount=amount, tendered=tendered, change=change, created_by=ctx.staff_id)
    s.add(p)
    order.paid_total += amount
    if order.shift_id is None:
        order.shift_id = shift.id
    await s.flush()
    await audit.record(ctx, "payment.added", "order", order.id, payment_id=p.id, method=method, amount=amount)

    if order.paid_total >= order.total:
        orders.mark_paid(order)
        await s.flush()
        payload = await receipt_payload(s, order, "receipt")
        s.add(FiscalReceipt(venue_id=ctx.venue_id, order_id=order.id, kind="sale", payload=rt._jsonable(payload)))
        await create_job(s, ctx.venue_id, "receipt", payload)
        await rt.emit(s, ctx.venue_id, rt.ORDER_PAID, orders._channels(order), orders.brief(order))
    else:
        await rt.emit(s, ctx.venue_id, rt.ORDER_UPDATED, orders._channels(order), orders.brief(order))
    return order, p


async def refund_payment(ctx: Ctx, payment_id: uuid.UUID, reason: str) -> Order:
    s = ctx.session
    p = await s.get(Payment, payment_id, with_for_update=True)
    if p is None:
        raise NotFound("payment")
    if p.refunded_at:
        raise AppError("conflict", 409, {"reason": "already_refunded"})
    order = await orders.get_order(s, p.order_id, lock=True)
    p.refunded_at = now()
    p.refunded_by = ctx.staff_id
    order.paid_total -= p.amount
    was_paid = order.status == ORDER_PAID
    if was_paid:
        order.status = ORDER_BILLED
        order.closed_at = None
        s.add(FiscalReceipt(venue_id=ctx.venue_id, order_id=order.id, kind="refund",
                            payload={"payment_id": str(p.id), "amount": p.amount, "method": p.method}))
    await s.flush()
    await audit.record(ctx, "payment.refunded", "order", order.id, payment_id=p.id, amount=p.amount, reason=reason)
    await rt.emit(s, ctx.venue_id, rt.ORDER_UPDATED, orders._channels(order), orders.brief(order))
    return order


async def send_fiscal(session: AsyncSession, receipt: FiscalReceipt) -> None:
    """Отправка чека в фискальный сервис (ККМ/ГНС).

    Провайдер ещё не выбран (открытый вопрос в архитектуре) — заглушка помечает чек отправленным.
    Подключение реального провайдера — реализация этой функции.
    """
    receipt.attempts += 1
    receipt.provider = "stub"
    receipt.status = "sent"
    receipt.sent_at = now()
    receipt.fiscal_number = f"STUB-{receipt.id.hex[:12].upper()}"
