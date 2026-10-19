import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.deps import Ctx, current_ctx, require
from app.core.errors import Forbidden, NotFound
from app.db import set_tenant
from app.modules.audit import service as audit
from app.modules.orders.models import ORDER_ACTIVE, Order
from app.modules.venues import service
from app.modules.venues.models import Hall, Table, Venue
from app.modules.venues.schemas import (
    HallIn,
    HallOut,
    HallUpdate,
    SettingsOut,
    SettingsUpdate,
    TableIn,
    TableOut,
    TableState,
    TableUpdate,
    VenueCreate,
    VenueOut,
    VenueUpdate,
)

router = APIRouter(tags=["venues"])


@router.get("/venue", response_model=VenueOut)
async def get_current_venue(ctx: Ctx = Depends(current_ctx)):
    return await service.get_venue(ctx.session, ctx.venue_id)


@router.patch("/venue", response_model=VenueOut)
async def update_current_venue(body: VenueUpdate, ctx: Ctx = Depends(require("venue.manage"))):
    venue = await service.get_venue(ctx.session, ctx.venue_id)
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(venue, k, v)
    await audit.record(ctx, "venue.updated", "venue", venue.id, **changes)
    return venue


@router.get("/venues", response_model=list[VenueOut])
async def list_org_venues(ctx: Ctx = Depends(current_ctx)):
    """Заведения сети (для владельца) или только текущее."""
    if not ctx.is_owner:
        return [await service.get_venue(ctx.session, ctx.venue_id)]
    current = await service.get_venue(ctx.session, ctx.venue_id)
    await set_tenant(ctx.session, None, bypass=True)
    rows = await ctx.session.scalars(select(Venue).where(Venue.organization_id == current.organization_id).order_by(Venue.name))
    result = list(rows.all())
    await set_tenant(ctx.session, ctx.venue_id)
    return result


@router.post("/venues", response_model=VenueOut, status_code=201)
async def create_org_venue(body: VenueCreate, ctx: Ctx = Depends(current_ctx)):
    if not ctx.is_owner:
        raise Forbidden(reason="owner_only")
    current = await service.get_venue(ctx.session, ctx.venue_id)
    await set_tenant(ctx.session, None, bypass=True)
    venue = await service.create_venue(ctx.session, current.organization_id, **body.model_dump())
    await set_tenant(ctx.session, ctx.venue_id)
    await audit.record(ctx, "venue.created", "venue", venue.id, slug=venue.slug)
    return venue


@router.get("/settings", response_model=SettingsOut)
async def get_settings(ctx: Ctx = Depends(current_ctx)):
    return await service.get_settings_row(ctx.session, ctx.venue_id)


@router.patch("/settings", response_model=SettingsOut)
async def update_settings(body: SettingsUpdate, ctx: Ctx = Depends(require("venue.manage"))):
    row = await service.get_settings_row(ctx.session, ctx.venue_id)
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(row, k, v)
    await audit.record(ctx, "settings.updated", "venue", ctx.venue_id, **changes)
    return row


@router.get("/halls", response_model=list[HallOut])
async def list_halls(ctx: Ctx = Depends(current_ctx)):
    return list((await ctx.session.scalars(select(Hall).order_by(Hall.sort_order, Hall.name))).all())


@router.post("/halls", response_model=HallOut, status_code=201)
async def create_hall(body: HallIn, ctx: Ctx = Depends(require("venue.manage"))):
    return await service.create_hall(ctx, body)


@router.patch("/halls/{hall_id}", response_model=HallOut)
async def update_hall(hall_id: uuid.UUID, body: HallUpdate, ctx: Ctx = Depends(require("venue.manage"))):
    return await service.update_hall(ctx, hall_id, body)


@router.get("/tables", response_model=list[TableState])
async def list_tables(hall_id: uuid.UUID | None = None, ctx: Ctx = Depends(current_ctx)):
    """Столы с состоянием (открытый заказ) — главный экран официанта."""
    stmt = (
        select(Table, Order.id, Order.status, Order.total, Order.waiter_id)
        .outerjoin(Order, (Order.table_id == Table.id) & Order.status.in_(ORDER_ACTIVE))
        .order_by(Table.sort_order, Table.name)
    )
    if hall_id:
        stmt = stmt.where(Table.hall_id == hall_id)
    out = []
    for table, oid, ostatus, ototal, waiter in (await ctx.session.execute(stmt)).all():
        st = TableState.model_validate(table)
        st.open_order_id, st.open_order_status, st.open_order_total, st.waiter_id = oid, ostatus, ototal, waiter
        out.append(st)
    return out


@router.post("/tables", response_model=TableOut, status_code=201)
async def create_table(body: TableIn, ctx: Ctx = Depends(require("venue.manage"))):
    return await service.create_table(ctx, body)


@router.patch("/tables/{table_id}", response_model=TableOut)
async def update_table(table_id: uuid.UUID, body: TableUpdate, ctx: Ctx = Depends(require("venue.manage"))):
    return await service.update_table(ctx, table_id, body)


@router.post("/tables/{table_id}/regenerate-qr", response_model=TableOut)
async def regenerate_qr(table_id: uuid.UUID, ctx: Ctx = Depends(require("venue.manage"))):
    return await service.regenerate_qr(ctx, table_id)


@router.get("/tables/by-qr/{token}", response_model=TableState)
async def table_by_qr(token: str, ctx: Ctx = Depends(current_ctx)):
    """QR-скан стола официантом. RLS не даст найти стол чужого заведения."""
    row = (
        await ctx.session.execute(
            select(Table, Order.id, Order.status, Order.total, Order.waiter_id)
            .outerjoin(Order, (Order.table_id == Table.id) & Order.status.in_(ORDER_ACTIVE))
            .where(Table.qr_token == token)
        )
    ).first()
    if row is None:
        raise NotFound("table")
    table, oid, ostatus, ototal, waiter = row
    st = TableState.model_validate(table)
    st.open_order_id, st.open_order_status, st.open_order_total, st.waiter_id = oid, ostatus, ototal, waiter
    return st
