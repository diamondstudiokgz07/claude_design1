import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.deps import Ctx, current_ctx, require
from app.modules.stations import service
from app.modules.stations.models import Station
from app.modules.stations.schemas import (
    MergeIn,
    SplitIn,
    StationIn,
    StationOpResult,
    StationOut,
    StationUpdate,
)

router = APIRouter(prefix="/stations", tags=["stations"])


@router.get("", response_model=list[StationOut])
async def list_stations(include_archived: bool = False, ctx: Ctx = Depends(current_ctx)):
    """Список станций — планшет кухни выбирает одну или несколько при входе."""
    stmt = select(Station).order_by(Station.sort_order, Station.name)
    if not include_archived:
        stmt = stmt.where(Station.archived_at.is_(None))
    return await service.stations_out(ctx, list((await ctx.session.scalars(stmt)).all()))


@router.post("", response_model=StationOut, status_code=201)
async def create_station(body: StationIn, ctx: Ctx = Depends(require("stations.manage"))):
    st = await service.create(ctx, body)
    return (await service.stations_out(ctx, [st]))[0]


@router.patch("/{station_id}", response_model=StationOut)
async def update_station(station_id: uuid.UUID, body: StationUpdate, ctx: Ctx = Depends(require("stations.manage"))):
    st = await service.update_station(ctx, station_id, body)
    return (await service.stations_out(ctx, [st]))[0]


@router.post("/{station_id}/split", response_model=StationOpResult)
async def split_station(station_id: uuid.UUID, body: SplitIn, ctx: Ctx = Depends(require("stations.manage"))):
    new, moved, moved_open, moved_cats = await service.split(ctx, station_id, body)
    return StationOpResult(station=(await service.stations_out(ctx, [new]))[0], moved_items=moved,
                           moved_open_order_items=moved_open, moved_categories=moved_cats)


@router.post("/{station_id}/merge", response_model=StationOpResult)
async def merge_station(station_id: uuid.UUID, body: MergeIn, ctx: Ctx = Depends(require("stations.manage"))):
    dst, moved, moved_open, moved_cats = await service.merge(ctx, station_id, body.target_id)
    return StationOpResult(station=(await service.stations_out(ctx, [dst]))[0], moved_items=moved,
                           moved_open_order_items=moved_open, moved_categories=moved_cats)


@router.delete("/{station_id}", response_model=StationOpResult)
async def delete_station(station_id: uuid.UUID, move_to: uuid.UUID | None = None,
                         ctx: Ctx = Depends(require("stations.manage"))):
    st, moved, moved_open, moved_cats = await service.delete(ctx, station_id, move_to)
    return StationOpResult(station=(await service.stations_out(ctx, [st]))[0], moved_items=moved,
                           moved_open_order_items=moved_open, moved_categories=moved_cats)
