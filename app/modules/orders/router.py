import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.core.deps import Ctx, require
from app.core.errors import Forbidden
from app.core.pagination import make_page, paginate_desc
from app.modules.orders import service
from app.modules.orders.models import (
    ITEM_COOKING,
    ITEM_QUEUED,
    ITEM_READY,
    ORDER_ACTIVE,
    Order,
    OrderItem,
    OrderItemModifier,
)
from app.modules.orders.schemas import (
    AddItemsIn,
    CancelIn,
    ItemStatusIn,
    KitchenItem,
    OrderBrief,
    OrderCreate,
    OrderItemModifierOut,
    OrderItemOut,
    OrderOut,
    OrderPage,
    TransferIn,
)
from app.modules.venues.models import Table

router = APIRouter(tags=["orders"])


@router.get("/orders", response_model=OrderPage)
async def list_orders(
    status: str | None = Query(default=None, description="active | open | billed | paid | cancelled | pending"),
    table_id: uuid.UUID | None = None,
    business_date: date | None = None,
    mine: bool = False,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    ctx: Ctx = Depends(require("orders.view")),
):
    stmt = select(Order)
    if status == "active":
        stmt = stmt.where(Order.status.in_(ORDER_ACTIVE))
    elif status:
        stmt = stmt.where(Order.status == status)
    if table_id:
        stmt = stmt.where(Order.table_id == table_id)
    if business_date:
        stmt = stmt.where(Order.business_date == business_date)
    if mine or not ctx.has("orders.view_all"):
        stmt = stmt.where(Order.waiter_id == ctx.staff_id)
    stmt = paginate_desc(stmt, Order.opened_at, Order.id, cursor, limit)
    rows, nxt = make_page(list((await ctx.session.scalars(stmt)).all()), limit, ts_attr="opened_at")
    return OrderPage(items=[OrderBrief.model_validate(r) for r in rows], next_cursor=nxt)


@router.post("/orders", response_model=OrderOut, status_code=201)
async def create_order(body: OrderCreate, ctx: Ctx = Depends(require("orders.create"))):
    order = await service.create_order(ctx, body, source="pos" if ctx.device_kind == "pos" else "waiter")
    return await service.order_out(ctx.session, order)


@router.get("/orders/{order_id}", response_model=OrderOut)
async def get_order(order_id: uuid.UUID, ctx: Ctx = Depends(require("orders.view"))):
    order = await service.get_order(ctx.session, order_id)
    if order.waiter_id not in (None, ctx.staff_id) and not ctx.has("orders.view_all"):
        raise Forbidden(reason="not_your_order")
    return await service.order_out(ctx.session, order)


@router.post("/orders/{order_id}/items", response_model=OrderOut)
async def add_items(order_id: uuid.UUID, body: AddItemsIn, ctx: Ctx = Depends(require("orders.create"))):
    order = await service.get_order(ctx.session, order_id, lock=True)
    service.ensure_can_edit(ctx, order)
    await service.add_items(ctx, order, body.items)
    return await service.order_out(ctx.session, order)


@router.post("/orders/{order_id}/confirm", response_model=OrderOut)
async def confirm_order(order_id: uuid.UUID, ctx: Ctx = Depends(require("orders.create"))):
    """Подтвердить заказ, пришедший с сайта (status=pending)."""
    order = await service.get_order(ctx.session, order_id, lock=True)
    await service.confirm_pending(ctx, order)
    return await service.order_out(ctx.session, order)


@router.patch("/order-items/{item_id}/status", response_model=OrderItemOut)
async def change_item_status(item_id: uuid.UUID, body: ItemStatusIn, ctx: Ctx = Depends(require())):
    item = await service.change_item_status(ctx, item_id, body.status, body.reason)
    out = OrderItemOut.model_validate(item)
    mods = (await ctx.session.scalars(select(OrderItemModifier).where(OrderItemModifier.order_item_id == item.id))).all()
    out.modifiers = [OrderItemModifierOut.model_validate(m) for m in mods]
    return out


@router.post("/orders/{order_id}/transfer", response_model=OrderOut)
async def transfer(order_id: uuid.UUID, body: TransferIn, ctx: Ctx = Depends(require("orders.transfer"))):
    order = await service.get_order(ctx.session, order_id, lock=True)
    await service.transfer(ctx, order, body.table_id, body.waiter_id)
    return await service.order_out(ctx.session, order)


@router.post("/orders/{order_id}/cancel", response_model=OrderOut)
async def cancel(order_id: uuid.UUID, body: CancelIn, ctx: Ctx = Depends(require())):
    order = await service.get_order(ctx.session, order_id, lock=True)
    await service.cancel_order(ctx, order, body.reason)
    return await service.order_out(ctx.session, order)


@router.get("/kitchen/items", response_model=list[KitchenItem])
async def kitchen_items(
    station_ids: list[uuid.UUID] = Query(default=[]),
    include_ready: bool = True,
    ctx: Ctx = Depends(require("kitchen.work")),
):
    """Экран станции: незакрытые позиции выбранных станций, от старых к новым (таймеры — по created_at)."""
    wanted = set(station_ids)
    if ctx.station_ids:  # повар привязан к станциям — видит только их
        wanted = wanted & set(ctx.station_ids) if wanted else set(ctx.station_ids)
    statuses = [ITEM_QUEUED, ITEM_COOKING] + ([ITEM_READY] if include_ready else [])
    stmt = (
        select(OrderItem, Order.number, Order.table_id, Table.name, Order.waiter_id, Order.comment)
        .join(Order, Order.id == OrderItem.order_id)
        .outerjoin(Table, Table.id == Order.table_id)
        .where(OrderItem.status.in_(statuses), Order.status != "pending")
        .order_by(OrderItem.created_at, OrderItem.id)
    )
    if wanted:
        stmt = stmt.where(OrderItem.station_id.in_(wanted))
    rows = (await ctx.session.execute(stmt)).all()
    ids = [r[0].id for r in rows]
    mods = (await ctx.session.scalars(select(OrderItemModifier).where(OrderItemModifier.order_item_id.in_(ids)))).all() if ids else []
    out = []
    for item, number, table_id, table_name, waiter_id, comment in rows:
        k = KitchenItem(**OrderItemOut.model_validate(item).model_dump(), order_number=number, table_id=table_id,
                        table_name=table_name, waiter_id=waiter_id, order_comment=comment)
        k.modifiers = [OrderItemModifierOut.model_validate(m) for m in mods if m.order_item_id == item.id]
        out.append(k)
    return out
