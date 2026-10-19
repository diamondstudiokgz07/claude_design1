import secrets
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Ctx
from app.core.errors import Conflict, NotFound
from app.core.security import hash_secret
from app.modules.audit import service as audit
from app.modules.venues.models import Hall, Organization, Table, Venue, VenueSettings


def new_qr_token() -> str:
    return secrets.token_urlsafe(16)


async def get_venue(session: AsyncSession, venue_id: uuid.UUID) -> Venue:
    venue = await session.get(Venue, venue_id)
    if venue is None:
        raise NotFound("venue")
    return venue


async def get_settings_row(session: AsyncSession, venue_id: uuid.UUID) -> VenueSettings:
    row = await session.get(VenueSettings, venue_id)
    if row is None:
        row = VenueSettings(venue_id=venue_id)
        session.add(row)
        await session.flush()
    return row


async def business_date(session: AsyncSession, venue_id: uuid.UUID, at: datetime | None = None) -> date:
    """Бизнес-день: до business_day_start_hour (по умолчанию 06:00) — ещё вчерашний день."""
    venue = await get_venue(session, venue_id)
    settings = await get_settings_row(session, venue_id)
    local = (at or datetime.now(ZoneInfo("UTC"))).astimezone(ZoneInfo(venue.timezone))
    return (local - timedelta(hours=settings.business_day_start_hour)).date()


async def create_venue(
    session: AsyncSession, organization_id: uuid.UUID, *, name: str, slug: str, timezone: str = "Asia/Bishkek",
    address: str | None = None, phone: str | None = None,
) -> Venue:
    """session должна быть системной (bypass) — заведение ещё не существует."""
    from app.modules.identity.service import seed_default_roles

    if await session.scalar(select(Venue.id).where(Venue.slug == slug)):
        raise Conflict("conflict", field="slug")
    venue = Venue(organization_id=organization_id, name=name, slug=slug, timezone=timezone, address=address, phone=phone)
    session.add(venue)
    await session.flush()
    session.add(VenueSettings(venue_id=venue.id))
    await seed_default_roles(session, venue.id)
    return venue


async def bootstrap(session: AsyncSession, data) -> tuple[Organization, Venue, uuid.UUID]:
    """Первичная настройка: организация, заведение, роли, владелец."""
    from app.modules.identity.models import Role, Staff

    org = Organization(name=data.organization)
    session.add(org)
    await session.flush()
    v = data.venue
    venue = await create_venue(session, org.id, name=v.name, slug=v.slug, timezone=v.timezone, address=v.address, phone=v.phone)
    admin_role = await session.scalar(select(Role).where(Role.venue_id == venue.id, Role.name == "admin"))
    assert admin_role
    owner = Staff(
        venue_id=venue.id, role_id=admin_role.id, name=data.owner_name, email=data.owner_email.lower(),
        password_hash=hash_secret(data.owner_password), is_owner=True,
    )
    session.add(owner)
    await session.flush()
    return org, venue, owner.id


# ---------- Залы и столы ----------


async def create_hall(ctx: Ctx, data) -> Hall:
    hall = Hall(id=data.id or uuid.uuid4(), venue_id=ctx.venue_id, name=data.name, sort_order=data.sort_order,
                is_active=data.is_active)
    ctx.session.add(hall)
    await ctx.session.flush()
    await audit.record(ctx, "hall.created", "hall", hall.id, name=hall.name)
    return hall


async def update_hall(ctx: Ctx, hall_id: uuid.UUID, data) -> Hall:
    hall = await ctx.session.get(Hall, hall_id)
    if hall is None:
        raise NotFound("hall")
    changes = data.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(hall, k, v)
    await audit.record(ctx, "hall.updated", "hall", hall_id, **changes)
    await ctx.session.flush()
    return hall


async def create_table(ctx: Ctx, data) -> Table:
    if await ctx.session.get(Hall, data.hall_id) is None:
        raise NotFound("hall")
    table = Table(id=data.id or uuid.uuid4(), venue_id=ctx.venue_id, hall_id=data.hall_id, name=data.name,
                  seats=data.seats, sort_order=data.sort_order, qr_token=new_qr_token())
    ctx.session.add(table)
    await ctx.session.flush()
    await audit.record(ctx, "table.created", "table", table.id, name=table.name)
    return table


async def update_table(ctx: Ctx, table_id: uuid.UUID, data) -> Table:
    table = await ctx.session.get(Table, table_id)
    if table is None:
        raise NotFound("table")
    changes = data.model_dump(exclude_unset=True)
    if "hall_id" in changes and await ctx.session.get(Hall, changes["hall_id"]) is None:
        raise NotFound("hall")
    for k, v in changes.items():
        setattr(table, k, v)
    await audit.record(ctx, "table.updated", "table", table_id, **changes)
    await ctx.session.flush()
    return table


async def regenerate_qr(ctx: Ctx, table_id: uuid.UUID) -> Table:
    table = await ctx.session.get(Table, table_id)
    if table is None:
        raise NotFound("table")
    table.qr_token = new_qr_token()
    await audit.record(ctx, "table.qr_regenerated", "table", table_id)
    await ctx.session.flush()
    return table
