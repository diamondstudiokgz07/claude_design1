import uuid

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy import select

from app.core.deps import Ctx, require
from app.core.errors import NotFound
from app.modules.menu import service
from app.modules.menu.models import Category, MenuItem, ModifierGroup
from app.modules.menu.schemas import (
    CategoryIn,
    CategoryOut,
    CategoryUpdate,
    ItemIn,
    ItemOut,
    ItemUpdate,
    ModifierGroupIn,
    ModifierGroupOut,
    StopIn,
)

router = APIRouter(tags=["menu"])


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(ctx: Ctx = Depends(require("menu.view"))):
    rows = await ctx.session.scalars(
        select(Category).where(Category.archived_at.is_(None)).order_by(Category.sort_order, Category.name)
    )
    return list(rows.all())


@router.post("/categories", response_model=CategoryOut, status_code=201)
async def create_category(body: CategoryIn, ctx: Ctx = Depends(require("menu.edit"))):
    return await service.create_category(ctx, body)


@router.patch("/categories/{category_id}", response_model=CategoryOut)
async def update_category(category_id: uuid.UUID, body: CategoryUpdate, ctx: Ctx = Depends(require("menu.edit"))):
    return await service.update_category(ctx, category_id, body)


@router.delete("/categories/{category_id}", status_code=204)
async def archive_category(category_id: uuid.UUID, ctx: Ctx = Depends(require("menu.edit"))):
    await service.archive_category(ctx, category_id)


@router.get("/items", response_model=list[ItemOut])
async def list_items(
    category_id: uuid.UUID | None = None,
    station_id: uuid.UUID | None = None,
    include_inactive: bool = False,
    ctx: Ctx = Depends(require("menu.view")),
):
    stmt = select(MenuItem).where(MenuItem.archived_at.is_(None)).order_by(MenuItem.sort_order, MenuItem.name)
    if category_id:
        stmt = stmt.where(MenuItem.category_id == category_id)
    if station_id:
        stmt = stmt.where(MenuItem.station_id == station_id)
    if not include_inactive:
        stmt = stmt.where(MenuItem.is_active.is_(True))
    return await service.items_out(ctx.session, list((await ctx.session.scalars(stmt)).all()))


@router.get("/items/{item_id}", response_model=ItemOut)
async def get_item(item_id: uuid.UUID, ctx: Ctx = Depends(require("menu.view"))):
    item = await ctx.session.get(MenuItem, item_id)
    if item is None or item.archived_at:
        raise NotFound("menu_item")
    return (await service.items_out(ctx.session, [item]))[0]


@router.post("/items", response_model=ItemOut, status_code=201)
async def create_item(body: ItemIn, ctx: Ctx = Depends(require("menu.edit"))):
    item = await service.create_item(ctx, body)
    return (await service.items_out(ctx.session, [item]))[0]


@router.patch("/items/{item_id}", response_model=ItemOut)
async def update_item(item_id: uuid.UUID, body: ItemUpdate, ctx: Ctx = Depends(require("menu.edit"))):
    item = await service.update_item(ctx, item_id, body)
    return (await service.items_out(ctx.session, [item]))[0]


@router.delete("/items/{item_id}", status_code=204)
async def archive_item(item_id: uuid.UUID, ctx: Ctx = Depends(require("menu.edit"))):
    await service.archive_item(ctx, item_id)


@router.post("/items/{item_id}/stop", response_model=ItemOut)
async def set_stop(item_id: uuid.UUID, body: StopIn, ctx: Ctx = Depends(require("menu.stop"))):
    """Стоп-лист: is_available=false — блюдо закончилось."""
    item = await service.set_stop(ctx, item_id, body.is_available)
    return (await service.items_out(ctx.session, [item]))[0]


@router.get("/stop-list", response_model=list[ItemOut])
async def stop_list(ctx: Ctx = Depends(require("menu.view"))):
    rows = await ctx.session.scalars(
        select(MenuItem).where(MenuItem.archived_at.is_(None), MenuItem.is_available.is_(False))
    )
    return await service.items_out(ctx.session, list(rows.all()))


@router.post("/items/{item_id}/photo", response_model=ItemOut)
async def upload_photo(item_id: uuid.UUID, file: UploadFile = File(...), ctx: Ctx = Depends(require("menu.edit"))):
    data = await file.read()
    item = await service.upload_photo(ctx, item_id, data, file.content_type or "")
    return (await service.items_out(ctx.session, [item]))[0]


@router.get("/modifiers", response_model=list[ModifierGroupOut])
async def list_modifier_groups(ctx: Ctx = Depends(require("menu.view"))):
    rows = await ctx.session.scalars(
        select(ModifierGroup).where(ModifierGroup.archived_at.is_(None)).order_by(ModifierGroup.sort_order, ModifierGroup.name)
    )
    return await service.groups_out(ctx.session, list(rows.all()))


@router.post("/modifiers", response_model=ModifierGroupOut, status_code=201)
async def create_modifier_group(body: ModifierGroupIn, ctx: Ctx = Depends(require("menu.edit"))):
    g = await service.create_group(ctx, body)
    return (await service.groups_out(ctx.session, [g]))[0]


@router.put("/modifiers/{group_id}", response_model=ModifierGroupOut)
async def replace_modifier_group(group_id: uuid.UUID, body: ModifierGroupIn, ctx: Ctx = Depends(require("menu.edit"))):
    g = await service.replace_group(ctx, group_id, body)
    return (await service.groups_out(ctx.session, [g]))[0]


@router.delete("/modifiers/{group_id}", status_code=204)
async def archive_modifier_group(group_id: uuid.UUID, ctx: Ctx = Depends(require("menu.edit"))):
    await service.archive_group(ctx, group_id)
