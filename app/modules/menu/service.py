import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storage
from app.core.deps import Ctx
from app.core.errors import AppError, NotFound
from app.core.models import File
from app.core.redis import get_redis
from app.core.security import now
from app.modules.audit import service as audit
from app.modules.menu.models import (
    Category,
    MenuItem,
    MenuItemModifierGroup,
    MenuItemTranslation,
    Modifier,
    ModifierGroup,
)
from app.modules.menu.schemas import (
    ItemOut,
    ModifierGroupOut,
    ModifierOut,
    TranslationIn,
)
from app.modules.realtime import service as rt
from app.modules.stations.models import Station


async def invalidate_public_cache(venue_id: uuid.UUID) -> None:
    """Сайт-меню кэширует ответ; при изменении меню/стоп-листа сбрасываем кэш."""
    r = get_redis()
    keys = [k async for k in r.scan_iter(f"public:menu:{venue_id}:*")]
    if keys:
        await r.delete(*keys)


async def _menu_changed(ctx: Ctx, **data) -> None:
    await rt.emit(ctx.session, ctx.venue_id, rt.MENU_CHANGED, [rt.ch_floor(ctx.venue_id), rt.ch_admin(ctx.venue_id)], data)
    await invalidate_public_cache(ctx.venue_id)


# ---------- Категории ----------


async def create_category(ctx: Ctx, data) -> Category:
    cat = Category(venue_id=ctx.venue_id, **{k: v for k, v in data.model_dump().items() if k != "id"})
    if data.id:
        cat.id = data.id
    ctx.session.add(cat)
    await ctx.session.flush()
    await audit.record(ctx, "category.created", "category", cat.id, name=cat.name)
    await _menu_changed(ctx, category_id=cat.id)
    return cat


async def update_category(ctx: Ctx, category_id: uuid.UUID, data) -> Category:
    cat = await ctx.session.get(Category, category_id)
    if cat is None or cat.archived_at:
        raise NotFound("category")
    changes = data.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(cat, k, v)
    await audit.record(ctx, "category.updated", "category", category_id, **changes)
    await _menu_changed(ctx, category_id=category_id)
    await ctx.session.flush()
    return cat


async def archive_category(ctx: Ctx, category_id: uuid.UUID) -> None:
    cat = await ctx.session.get(Category, category_id)
    if cat is None or cat.archived_at:
        raise NotFound("category")
    has_items = await ctx.session.scalar(
        select(MenuItem.id).where(MenuItem.category_id == category_id, MenuItem.archived_at.is_(None)).limit(1)
    )
    if has_items:
        raise AppError("conflict", 409, {"reason": "category_not_empty"})
    cat.archived_at = now()
    await audit.record(ctx, "category.archived", "category", category_id)
    await _menu_changed(ctx, category_id=category_id)


# ---------- Блюда ----------


async def items_out(session: AsyncSession, items: list[MenuItem]) -> list[ItemOut]:
    if not items:
        return []
    ids = [i.id for i in items]
    trs = (await session.scalars(select(MenuItemTranslation).where(MenuItemTranslation.item_id.in_(ids)))).all()
    groups = (await session.execute(
        select(MenuItemModifierGroup.item_id, MenuItemModifierGroup.group_id).where(MenuItemModifierGroup.item_id.in_(ids))
    )).all()
    file_ids = [i.photo_file_id for i in items if i.photo_file_id]
    files = {f.id: f for f in (await session.scalars(select(File).where(File.id.in_(file_ids)))).all()} if file_ids else {}
    station_ids = {i.station_id for i in items}
    active_stations = set(
        (await session.scalars(select(Station.id).where(Station.id.in_(station_ids), Station.is_active.is_(True),
                                                        Station.archived_at.is_(None)))).all()
    )
    out = []
    for i in items:
        o = ItemOut.model_validate(i)
        o.translations = {t.lang: TranslationIn(name=t.name, description=t.description) for t in trs if t.item_id == i.id}
        o.modifier_group_ids = [g for (iid, g) in groups if iid == i.id]
        o.available_now = i.is_available and i.station_id in active_stations
        f = files.get(i.photo_file_id) if i.photo_file_id else None
        if f:
            o.photo_url = storage.public_url(f.key)
            o.photo_thumb_url = storage.public_url(f.thumb_key)
        out.append(o)
    return out


async def _set_translations(ctx: Ctx, item_id: uuid.UUID, translations: dict[str, TranslationIn]) -> None:
    await ctx.session.execute(delete(MenuItemTranslation).where(MenuItemTranslation.item_id == item_id))
    for lang, tr in translations.items():
        ctx.session.add(MenuItemTranslation(item_id=item_id, lang=lang, venue_id=ctx.venue_id, name=tr.name,
                                            description=tr.description))


async def _set_groups(ctx: Ctx, item_id: uuid.UUID, group_ids: list[uuid.UUID]) -> None:
    await ctx.session.execute(delete(MenuItemModifierGroup).where(MenuItemModifierGroup.item_id == item_id))
    for gid in dict.fromkeys(group_ids):
        if await ctx.session.get(ModifierGroup, gid) is None:
            raise NotFound("modifier_group", id=str(gid))
        ctx.session.add(MenuItemModifierGroup(item_id=item_id, group_id=gid, venue_id=ctx.venue_id))


async def _resolve_station(ctx: Ctx, category: Category, station_id: uuid.UUID | None) -> uuid.UUID:
    sid = station_id or category.default_station_id
    if sid is None:
        raise AppError("validation_error", 422, {"field": "station_id", "reason": "required"})
    st = await ctx.session.get(Station, sid)
    if st is None or st.archived_at:
        raise NotFound("station")
    return sid


async def create_item(ctx: Ctx, data) -> MenuItem:
    cat = await ctx.session.get(Category, data.category_id)
    if cat is None or cat.archived_at:
        raise NotFound("category")
    item = MenuItem(
        id=data.id or uuid.uuid4(), venue_id=ctx.venue_id, category_id=cat.id,
        station_id=await _resolve_station(ctx, cat, data.station_id),
        name=data.name, description=data.description, price=data.price, weight_grams=data.weight_grams,
        cook_time_min=data.cook_time_min, is_active=data.is_active, sort_order=data.sort_order,
    )
    ctx.session.add(item)
    await ctx.session.flush()
    await _set_translations(ctx, item.id, data.translations)
    await _set_groups(ctx, item.id, data.modifier_group_ids)
    await audit.record(ctx, "menu_item.created", "menu_item", item.id, name=item.name, price=item.price)
    await _menu_changed(ctx, item_id=item.id)
    await ctx.session.flush()
    return item


async def update_item(ctx: Ctx, item_id: uuid.UUID, data) -> MenuItem:
    item = await ctx.session.get(MenuItem, item_id)
    if item is None or item.archived_at:
        raise NotFound("menu_item")
    changes = data.model_dump(exclude_unset=True)
    translations = changes.pop("translations", None)
    group_ids = changes.pop("modifier_group_ids", None)
    if "category_id" in changes:
        cat = await ctx.session.get(Category, changes["category_id"])
        if cat is None or cat.archived_at:
            raise NotFound("category")
    if changes.get("station_id"):
        st = await ctx.session.get(Station, changes["station_id"])
        if st is None or st.archived_at:
            raise NotFound("station")
    old_price = item.price
    for k, v in changes.items():
        if v is None and k in ("station_id", "category_id", "price"):
            continue
        setattr(item, k, v)
    if translations is not None:
        await _set_translations(ctx, item_id, data.translations)
    if group_ids is not None:
        await _set_groups(ctx, item_id, group_ids)
    if "price" in changes and changes["price"] != old_price:
        await audit.record(ctx, "menu_item.price_changed", "menu_item", item_id, before=old_price, after=item.price)
    await audit.record(ctx, "menu_item.updated", "menu_item", item_id, **{k: v for k, v in changes.items()})
    await _menu_changed(ctx, item_id=item_id)
    await ctx.session.flush()
    return item


async def archive_item(ctx: Ctx, item_id: uuid.UUID) -> None:
    item = await ctx.session.get(MenuItem, item_id)
    if item is None or item.archived_at:
        raise NotFound("menu_item")
    item.archived_at = now()
    item.is_active = False
    await audit.record(ctx, "menu_item.archived", "menu_item", item_id)
    await _menu_changed(ctx, item_id=item_id)


async def emit_stop_list(ctx: Ctx, items: list[MenuItem]) -> None:
    channels = [rt.ch_floor(ctx.venue_id), rt.ch_admin(ctx.venue_id)] + [rt.ch_station(i.station_id) for i in items]
    await rt.emit(ctx.session, ctx.venue_id, rt.STOP_LIST, channels,
                  {"items": [{"id": i.id, "is_available": i.is_available} for i in items]})
    await invalidate_public_cache(ctx.venue_id)


async def set_stop(ctx: Ctx, item_id: uuid.UUID, is_available: bool) -> MenuItem:
    """Стоп-лист = menu_items.is_available + история в audit_log."""
    item = await ctx.session.get(MenuItem, item_id)
    if item is None or item.archived_at:
        raise NotFound("menu_item")
    if item.is_available != is_available:
        item.is_available = is_available
        await audit.record(ctx, "stop_list.changed", "menu_item", item_id, name=item.name, is_available=is_available)
        await emit_stop_list(ctx, [item])
    await ctx.session.flush()
    return item


async def upload_photo(ctx: Ctx, item_id: uuid.UUID, data: bytes, content_type: str) -> MenuItem:
    item = await ctx.session.get(MenuItem, item_id)
    if item is None or item.archived_at:
        raise NotFound("menu_item")
    if content_type not in storage.ALLOWED_IMAGE_TYPES or len(data) > storage.MAX_IMAGE_BYTES:
        raise AppError("validation_error", 422, {"field": "file", "reason": "image jpeg/png/webp up to 8MB"})
    try:
        full, thumb = storage.make_webp_versions(data)
    except Exception as e:  # noqa: BLE001
        raise AppError("validation_error", 422, {"field": "file", "reason": "not an image"}) from e
    fid = uuid.uuid4()
    key = f"venues/{ctx.venue_id}/menu/{fid}.webp"
    thumb_key = f"venues/{ctx.venue_id}/menu/{fid}_400.webp"
    await storage.put(key, full, "image/webp")
    await storage.put(thumb_key, thumb, "image/webp")
    ctx.session.add(File(id=fid, venue_id=ctx.venue_id, key=key, thumb_key=thumb_key, content_type="image/webp",
                         size=len(full), kind="photo", created_by=ctx.staff_id))
    await ctx.session.flush()
    item.photo_file_id = fid
    await audit.record(ctx, "menu_item.photo", "menu_item", item_id, file_id=fid)
    await _menu_changed(ctx, item_id=item_id)
    await ctx.session.flush()
    return item


# ---------- Модификаторы ----------


async def groups_out(session: AsyncSession, groups: list[ModifierGroup]) -> list[ModifierGroupOut]:
    ids = [g.id for g in groups]
    mods = (await session.scalars(
        select(Modifier).where(Modifier.group_id.in_(ids), Modifier.archived_at.is_(None)).order_by(Modifier.sort_order, Modifier.name)
    )).all() if ids else []
    out = []
    for g in groups:
        o = ModifierGroupOut.model_validate(g)
        o.modifiers = [ModifierOut.model_validate(m) for m in mods if m.group_id == g.id]
        out.append(o)
    return out


async def create_group(ctx: Ctx, data) -> ModifierGroup:
    if data.min_select > data.max_select:
        raise AppError("validation_error", 422, {"field": "min_select"})
    g = ModifierGroup(id=data.id or uuid.uuid4(), venue_id=ctx.venue_id, name=data.name, translations=data.translations,
                      min_select=data.min_select, max_select=data.max_select, sort_order=data.sort_order)
    ctx.session.add(g)
    await ctx.session.flush()
    for m in data.modifiers:
        ctx.session.add(Modifier(id=m.id or uuid.uuid4(), venue_id=ctx.venue_id, group_id=g.id, name=m.name,
                                 translations=m.translations, price=m.price, is_available=m.is_available,
                                 sort_order=m.sort_order))
    await audit.record(ctx, "modifier_group.created", "modifier_group", g.id, name=g.name)
    await _menu_changed(ctx, modifier_group_id=g.id)
    await ctx.session.flush()
    return g


async def replace_group(ctx: Ctx, group_id: uuid.UUID, data) -> ModifierGroup:
    """Полная замена группы: модификаторы, которых нет в запросе, архивируются."""
    g = await ctx.session.get(ModifierGroup, group_id)
    if g is None or g.archived_at:
        raise NotFound("modifier_group")
    if data.min_select > data.max_select:
        raise AppError("validation_error", 422, {"field": "min_select"})
    g.name, g.translations, g.min_select, g.max_select, g.sort_order = (
        data.name, data.translations, data.min_select, data.max_select, data.sort_order)
    existing = {m.id: m for m in (await ctx.session.scalars(
        select(Modifier).where(Modifier.group_id == group_id, Modifier.archived_at.is_(None)))).all()}
    keep = set()
    for m in data.modifiers:
        if m.id and m.id in existing:
            row = existing[m.id]
            row.name, row.translations, row.price, row.is_available, row.sort_order = (
                m.name, m.translations, m.price, m.is_available, m.sort_order)
            keep.add(m.id)
        else:
            ctx.session.add(Modifier(id=m.id or uuid.uuid4(), venue_id=ctx.venue_id, group_id=g.id, name=m.name,
                                     translations=m.translations, price=m.price, is_available=m.is_available,
                                     sort_order=m.sort_order))
    for mid, row in existing.items():
        if mid not in keep:
            row.archived_at = now()
    await audit.record(ctx, "modifier_group.updated", "modifier_group", group_id)
    await _menu_changed(ctx, modifier_group_id=group_id)
    await ctx.session.flush()
    return g


async def archive_group(ctx: Ctx, group_id: uuid.UUID) -> None:
    g = await ctx.session.get(ModifierGroup, group_id)
    if g is None or g.archived_at:
        raise NotFound("modifier_group")
    g.archived_at = now()
    await ctx.session.execute(delete(MenuItemModifierGroup).where(MenuItemModifierGroup.group_id == group_id))
    await audit.record(ctx, "modifier_group.archived", "modifier_group", group_id)
    await _menu_changed(ctx, modifier_group_id=group_id)


# ---------- Для модуля orders ----------


async def resolve_for_order(
    session: AsyncSession, menu_item_id: uuid.UUID, modifier_ids: list[uuid.UUID]
) -> tuple[MenuItem, list[Modifier]]:
    """Проверяет, что блюдо доступно и выбор модификаторов корректен."""
    item = await session.get(MenuItem, menu_item_id)
    if item is None or item.archived_at or not item.is_active:
        raise NotFound("menu_item", id=str(menu_item_id))
    station = await session.get(Station, item.station_id)
    if not item.is_available or station is None or not station.is_active or station.archived_at:
        raise AppError("item_unavailable", 409, {"menu_item_id": str(menu_item_id), "name": item.name})

    group_ids = list((await session.scalars(
        select(MenuItemModifierGroup.group_id).where(MenuItemModifierGroup.item_id == menu_item_id))).all())
    groups = {g.id: g for g in (await session.scalars(
        select(ModifierGroup).where(ModifierGroup.id.in_(group_ids), ModifierGroup.archived_at.is_(None)))).all()} if group_ids else {}
    mods = list((await session.scalars(select(Modifier).where(Modifier.id.in_(modifier_ids)))).all()) if modifier_ids else []
    if len(mods) != len(set(modifier_ids)):
        raise AppError("invalid_modifiers", 422, {"reason": "unknown_modifier"})
    for m in mods:
        if m.group_id not in groups or m.archived_at:
            raise AppError("invalid_modifiers", 422, {"reason": "not_for_item", "modifier_id": str(m.id)})
        if not m.is_available:
            raise AppError("item_unavailable", 409, {"modifier_id": str(m.id), "name": m.name})
    for g in groups.values():
        n = sum(1 for m in mods if m.group_id == g.id)
        if n < g.min_select or n > g.max_select:
            raise AppError("invalid_modifiers", 422, {"group_id": str(g.id), "min": g.min_select, "max": g.max_select})
    return item, mods
