import uuid

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.deps import Ctx, client_ip, current_ctx, db_system, require
from app.core.errors import NotFound
from app.core.permissions import PERMISSIONS
from app.core.redis import rate_limit
from app.modules.identity import service
from app.modules.identity.models import Device, Role, Staff
from app.modules.identity.schemas import (
    DeviceOut,
    LoginIn,
    MeOut,
    PairIn,
    PairingCodeIn,
    PairingCodeOut,
    PairOut,
    PermissionOut,
    PinIn,
    RefreshIn,
    RoleIn,
    RoleOut,
    RolePermissionsIn,
    StaffCreate,
    StaffOut,
    StaffPick,
    StaffPinIn,
    StaffStationsIn,
    StaffUpdate,
    SwitchVenueIn,
    TokensOut,
    TotpConfirmIn,
    TotpSetupOut,
    VenueBrief,
)
from app.modules.venues.models import Venue, VenueSettings

auth = APIRouter(prefix="/auth", tags=["auth"])
staff_router = APIRouter(tags=["staff"])


# ---------------- auth ----------------


@auth.post("/device/pairing-codes", response_model=PairingCodeOut, status_code=201)
async def create_pairing_code(body: PairingCodeIn, ctx: Ctx = Depends(require("devices.manage"))):
    """Админ в «Кассе» создаёт одноразовый код (QR, 10 минут)."""
    device, code = await service.create_pairing_code(ctx, body.name, body.kind)
    assert device.pairing_expires_at
    return PairingCodeOut(device_id=device.id, code=code, expires_at=device.pairing_expires_at)


@auth.post("/device/pair", response_model=PairOut)
async def pair(body: PairIn, request: Request, session: AsyncSession = Depends(db_system)):
    ip = client_ip(request)
    await rate_limit(f"pair:{ip}", 10, 60)
    device, venue, token = await service.pair_device(session, body.code, body.platform, body.app_version, ip)
    return PairOut(device_token=token, device=DeviceOut.model_validate(device), venue=VenueBrief.model_validate(venue))


@auth.get("/device/staff", response_model=list[StaffPick])
async def device_staff(
    session: AsyncSession = Depends(db_system), x_device_token: str | None = Header(default=None)
):
    """Список сотрудников для экрана выбора на привязанном устройстве."""
    device = await service.device_by_token(session, x_device_token)
    rows = await session.scalars(
        select(Staff)
        .where(Staff.venue_id == device.venue_id, Staff.is_active.is_(True), Staff.pin_hash.is_not(None))
        .order_by(Staff.name)
    )
    return list(rows.all())


@auth.post("/pin", response_model=TokensOut)
async def pin_login(
    body: PinIn,
    request: Request,
    session: AsyncSession = Depends(db_system),
    x_device_token: str | None = Header(default=None),
):
    device = await service.device_by_token(session, x_device_token)
    return await service.pin_login(session, device, body.staff_id, body.pin, client_ip(request))


@auth.post("/login", response_model=TokensOut)
async def login(body: LoginIn, request: Request, session: AsyncSession = Depends(db_system)):
    """Владелец и админ «с дома»: email/телефон + пароль + 2FA."""
    ip = client_ip(request)
    await rate_limit(f"login:{ip}", 10, 60)
    return await service.password_login(
        session, email=body.email, phone=body.phone, password=body.password, totp_code=body.totp_code,
        venue_id=body.venue_id, ip=ip,
    )


@auth.post("/refresh", response_model=TokensOut)
async def refresh(body: RefreshIn, session: AsyncSession = Depends(db_system)):
    return await service.refresh_tokens(session, body.refresh_token)


@auth.post("/logout", status_code=204)
async def logout(ctx: Ctx = Depends(current_ctx)):
    await service.logout(ctx)


@auth.post("/switch-venue", response_model=TokensOut)
async def switch_venue(body: SwitchVenueIn, ctx: Ctx = Depends(current_ctx)):
    """Владелец сети переключается на другое заведение."""
    from app.db import set_tenant

    await set_tenant(ctx.session, None, bypass=True)
    staff = await ctx.session.get(Staff, ctx.staff_id)
    assert staff is not None
    await service.ensure_owner_access(ctx.session, staff, body.venue_id)
    return await service.issue_tokens(ctx.session, staff=staff, venue_id=body.venue_id, device_id=None)


@auth.get("/me", response_model=MeOut)
async def me(ctx: Ctx = Depends(current_ctx)):
    from app.db import set_tenant

    venue = await ctx.session.get(Venue, ctx.venue_id)
    settings = await ctx.session.get(VenueSettings, ctx.venue_id)
    device = await ctx.session.get(Device, ctx.device_id) if ctx.device_id else None
    await set_tenant(ctx.session, None, bypass=True)  # владелец может быть из другого заведения
    staff = await ctx.session.get(Staff, ctx.staff_id)
    await set_tenant(ctx.session, ctx.venue_id)
    return MeOut(
        staff=StaffPick.model_validate(staff),
        is_owner=ctx.is_owner,
        venue=VenueBrief.model_validate(venue),
        device=DeviceOut.model_validate(device) if device else None,
        permissions=sorted(ctx.permissions),
        station_ids=ctx.station_ids,
        min_app_version=(settings.min_app_version if settings and settings.min_app_version else get_settings().min_app_version),
    )


@auth.post("/totp/setup", response_model=TotpSetupOut)
async def totp_setup(ctx: Ctx = Depends(current_ctx)):
    secret, uri = await service.totp_setup(ctx)
    return TotpSetupOut(secret=secret, otpauth_uri=uri)


@auth.post("/totp/confirm", status_code=204)
async def totp_confirm(body: TotpConfirmIn, ctx: Ctx = Depends(current_ctx)):
    await service.totp_confirm(ctx, body.code)


# ---------------- сотрудники, роли, устройства ----------------


async def _staff_out(ctx: Ctx, rows: list[Staff]) -> list[StaffOut]:
    stations = await service.staff_stations(ctx.session, [s.id for s in rows])
    out = []
    for s in rows:
        o = StaffOut.model_validate(s)
        o.has_pin = bool(s.pin_hash)
        o.station_ids = stations.get(s.id, [])
        out.append(o)
    return out


@staff_router.get("/staff", response_model=list[StaffOut])
async def list_staff(ctx: Ctx = Depends(require("staff.manage"))):
    rows = list((await ctx.session.scalars(select(Staff).order_by(Staff.name))).all())
    return await _staff_out(ctx, rows)


@staff_router.post("/staff", response_model=StaffOut, status_code=201)
async def create_staff(body: StaffCreate, ctx: Ctx = Depends(require("staff.manage"))):
    staff = await service.create_staff(ctx, body)
    return (await _staff_out(ctx, [staff]))[0]


@staff_router.patch("/staff/{staff_id}", response_model=StaffOut)
async def update_staff(staff_id: uuid.UUID, body: StaffUpdate, ctx: Ctx = Depends(require("staff.manage"))):
    staff = await service.update_staff(ctx, staff_id, body)
    return (await _staff_out(ctx, [staff]))[0]


@staff_router.put("/staff/{staff_id}/pin", status_code=204)
async def set_pin(staff_id: uuid.UUID, body: StaffPinIn, ctx: Ctx = Depends(require("staff.manage"))):
    await service.set_staff_pin(ctx, staff_id, body.pin)


@staff_router.put("/staff/{staff_id}/stations", status_code=204)
async def set_stations(staff_id: uuid.UUID, body: StaffStationsIn, ctx: Ctx = Depends(require("staff.manage"))):
    await service.set_staff_stations(ctx, staff_id, body.station_ids)


@staff_router.get("/permissions", response_model=list[PermissionOut])
async def list_permissions(ctx: Ctx = Depends(require("staff.manage"))):
    return [PermissionOut(code=k, title=v) for k, v in PERMISSIONS.items()]


@staff_router.get("/roles", response_model=list[RoleOut])
async def list_roles(ctx: Ctx = Depends(require("staff.manage"))):
    roles = list((await ctx.session.scalars(select(Role).order_by(Role.name))).all())
    perms = await service.role_permissions(ctx.session, [r.id for r in roles])
    return [RoleOut(id=r.id, name=r.name, is_system=r.is_system, permissions=perms[r.id]) for r in roles]


@staff_router.post("/roles", response_model=RoleOut, status_code=201)
async def create_role(body: RoleIn, ctx: Ctx = Depends(require("staff.manage"))):
    role = await service.create_role(ctx, body.name, body.permissions)
    return RoleOut(id=role.id, name=role.name, is_system=False, permissions=sorted(set(body.permissions)))


@staff_router.put("/roles/{role_id}/permissions", response_model=RoleOut)
async def set_role_permissions(role_id: uuid.UUID, body: RolePermissionsIn, ctx: Ctx = Depends(require("staff.manage"))):
    perms = await service.set_role_permissions(ctx, role_id, body.permissions)
    role = await ctx.session.get(Role, role_id)
    assert role
    return RoleOut(id=role.id, name=role.name, is_system=role.is_system, permissions=perms)


@staff_router.delete("/roles/{role_id}", status_code=204)
async def delete_role(role_id: uuid.UUID, ctx: Ctx = Depends(require("staff.manage"))):
    await service.delete_role(ctx, role_id)


@staff_router.get("/devices", response_model=list[DeviceOut])
async def list_devices(ctx: Ctx = Depends(require("devices.manage"))):
    rows = await ctx.session.scalars(select(Device).where(Device.paired_at.is_not(None)).order_by(Device.created_at.desc()))
    return list(rows.all())


@staff_router.post("/devices/{device_id}/revoke", response_model=DeviceOut)
async def revoke_device(device_id: uuid.UUID, ctx: Ctx = Depends(require("devices.manage"))):
    device = await service.revoke_device(ctx, device_id)
    if device is None:
        raise NotFound("device")
    return device
