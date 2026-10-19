"""Сервис identity: устройства, вход по PIN/паролю, токены, сотрудники и роли."""

import uuid
from datetime import timedelta

import pyotp
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.deps import Ctx
from app.core.errors import AppError, Conflict, NotFound
from app.core.permissions import DEFAULT_ROLES, PERMISSIONS
from app.core.redis import get_redis, hit_limit
from app.core.security import (
    create_access_token,
    hash_secret,
    new_pairing_code,
    new_token,
    now,
    sha256,
    verify_secret,
)
from app.db import tenant_session
from app.modules.audit import service as audit
from app.modules.audit.models import AuditLog
from app.modules.identity.models import (
    Device,
    RefreshToken,
    Role,
    RolePermission,
    Staff,
    StaffStation,
)
from app.modules.identity.schemas import TokensOut
from app.modules.realtime import service as rt
from app.modules.venues.models import Venue

# ---------- Устройства ----------


async def create_pairing_code(ctx: Ctx, name: str, kind: str) -> tuple[Device, str]:
    s = get_settings()
    code = new_pairing_code()
    device = Device(
        venue_id=ctx.venue_id,
        name=name,
        kind=kind,
        pairing_code_hash=sha256(code),
        pairing_expires_at=now() + timedelta(minutes=s.pairing_code_ttl_min),
        created_by=ctx.staff_id,
    )
    ctx.session.add(device)
    await ctx.session.flush()
    await audit.record(ctx, "device.pairing_code", "device", device.id, name=name, kind=kind)
    return device, code


async def pair_device(
    session: AsyncSession, code: str, platform: str | None, app_version: str | None, ip: str | None
) -> tuple[Device, Venue, str]:
    """session — системная (без RLS): заведение определяется по коду."""
    device = await session.scalar(
        select(Device).where(Device.pairing_code_hash == sha256(code.strip().upper())).with_for_update()
    )
    if device is None or device.revoked_at or device.pairing_expires_at is None or device.pairing_expires_at < now():
        raise AppError("invalid_pairing_code", 400)
    token = new_token(32)
    device.token_hash = sha256(token)
    device.pairing_code_hash = None
    device.pairing_expires_at = None
    device.paired_at = now()
    device.last_seen_at = now()
    device.platform = platform
    device.app_version = app_version
    venue = await session.get(Venue, device.venue_id)
    assert venue is not None
    session.add(AuditLog(venue_id=device.venue_id, device_id=device.id, action="device.paired", entity="device",
                         entity_id=str(device.id), data={"platform": platform}, ip=ip))
    return device, venue, token


async def device_by_token(session: AsyncSession, token: str | None) -> Device:
    if not token:
        raise AppError("device_required", 401)
    device = await session.scalar(select(Device).where(Device.token_hash == sha256(token)))
    if device is None:
        raise AppError("device_required", 401)
    if device.revoked_at is not None:
        raise AppError("device_revoked", 401)
    return device


async def revoke_device(ctx: Ctx, device_id: uuid.UUID) -> Device:
    device = await ctx.session.get(Device, device_id)
    if device is None:
        raise NotFound("device")
    if device.revoked_at is None:
        device.revoked_at = now()
        device.token_hash = None
        await ctx.session.execute(
            update(RefreshToken)
            .where(RefreshToken.device_id == device_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now())
        )
        await rt.emit(ctx.session, ctx.venue_id, rt.SESSION_REVOKED, [rt.ch_device(device_id)], {"device_id": device_id})
        await audit.record(ctx, "device.revoked", "device", device_id)
    return device


# ---------- Токены ----------


async def issue_tokens(
    session: AsyncSession, *, staff: Staff, venue_id: uuid.UUID, device_id: uuid.UUID | None
) -> TokensOut:
    s = get_settings()
    ttl = timedelta(hours=s.refresh_token_ttl_hours if device_id else s.owner_refresh_token_ttl_hours)
    refresh = new_token(48)
    rt_row = RefreshToken(
        venue_id=venue_id, staff_id=staff.id, device_id=device_id, token_hash=sha256(refresh), expires_at=now() + ttl
    )
    session.add(rt_row)
    await session.flush()
    access, exp = create_access_token(staff_id=staff.id, venue_id=venue_id, session_id=rt_row.id, device_id=device_id)
    return TokensOut(access_token=access, access_expires_at=exp, refresh_token=refresh, refresh_expires_at=rt_row.expires_at)


async def refresh_tokens(session: AsyncSession, refresh: str) -> TokensOut:
    row = await session.scalar(select(RefreshToken).where(RefreshToken.token_hash == sha256(refresh)).with_for_update())
    if row is None:
        raise AppError("unauthorized", 401)
    if row.revoked_at is not None:
        if row.replaced_by is not None:
            # Повторное использование уже обменянного токена — вероятная кража: отзываем все сессии устройства.
            # Отдельная транзакция: текущая откатится вместе с ошибкой.
            device_cond = (RefreshToken.device_id == row.device_id) if row.device_id else RefreshToken.device_id.is_(None)
            async with tenant_session(row.venue_id) as s2:
                await s2.execute(
                    update(RefreshToken)
                    .where(RefreshToken.staff_id == row.staff_id, device_cond, RefreshToken.revoked_at.is_(None))
                    .values(revoked_at=now())
                )
                s2.add(AuditLog(venue_id=row.venue_id, staff_id=row.staff_id, device_id=row.device_id,
                                action="auth.refresh_reuse", entity="staff", entity_id=str(row.staff_id), data={}))
        raise AppError("unauthorized", 401)
    if row.expires_at < now():
        raise AppError("token_expired", 401)
    staff = await session.get(Staff, row.staff_id)
    if staff is None or not staff.is_active:
        raise AppError("unauthorized", 401)
    if row.device_id:
        device = await session.get(Device, row.device_id)
        if device is None or device.revoked_at:
            raise AppError("device_revoked", 401)
        device.last_seen_at = now()
    # Новый refresh не продлевает смену: срок как у исходного
    new_refresh = new_token(48)
    new_row = RefreshToken(
        venue_id=row.venue_id, staff_id=row.staff_id, device_id=row.device_id,
        token_hash=sha256(new_refresh), expires_at=row.expires_at,
    )
    session.add(new_row)
    await session.flush()
    row.revoked_at = now()
    row.replaced_by = new_row.id
    access, exp = create_access_token(
        staff_id=row.staff_id, venue_id=row.venue_id, session_id=new_row.id, device_id=row.device_id
    )
    return TokensOut(access_token=access, access_expires_at=exp, refresh_token=new_refresh, refresh_expires_at=new_row.expires_at)


async def logout(ctx: Ctx) -> None:
    await ctx.session.execute(
        update(RefreshToken).where(RefreshToken.id == ctx.session_id).values(revoked_at=now())
    )
    await audit.record(ctx, "auth.logout", "staff", ctx.staff_id)


# ---------- Вход по PIN ----------


async def pin_login(session: AsyncSession, device: Device, staff_id: uuid.UUID, pin: str, ip: str | None) -> TokensOut:
    s = get_settings()
    r = get_redis()
    lock_key = f"pin:lock:staff:{staff_id}"
    dev_lock_key = f"pin:lock:device:{device.id}"
    if await r.exists(lock_key) or await r.exists(dev_lock_key):
        raise AppError("pin_locked", 429)

    staff = await session.get(Staff, staff_id)
    ok = (
        staff is not None
        and staff.venue_id == device.venue_id
        and staff.is_active
        and verify_secret(staff.pin_hash, pin)
    )
    if not ok:
        window = s.pin_lock_minutes * 60
        n_staff = await hit_limit(f"pin:fail:staff:{staff_id}", s.pin_max_attempts_staff, window)
        n_dev = await hit_limit(f"pin:fail:device:{device.id}", s.pin_max_attempts_device, window)
        locked = None
        if n_staff >= s.pin_max_attempts_staff:
            await r.set(lock_key, "1", ex=window)
            locked = "staff"
        if n_dev >= s.pin_max_attempts_device:
            await r.set(dev_lock_key, "1", ex=window)
            locked = "device"
        # Запрос завершится ошибкой и его транзакция откатится, поэтому журнал пишем отдельно.
        known_staff = staff_id if staff is not None and staff.venue_id == device.venue_id else None
        async with tenant_session(device.venue_id) as s2:
            s2.add(AuditLog(venue_id=device.venue_id, staff_id=known_staff, device_id=device.id,
                            action="auth.pin_failed", entity="staff", entity_id=str(staff_id), data={}, ip=ip))
            if locked:
                s2.add(AuditLog(venue_id=device.venue_id, staff_id=known_staff, device_id=device.id,
                                action="auth.pin_locked", entity="staff", entity_id=str(staff_id),
                                data={"scope": locked}, ip=ip))
                await rt.emit(s2, device.venue_id, rt.SECURITY_ALERT, [rt.ch_admin(device.venue_id)],
                              {"kind": "pin_locked", "scope": locked, "staff_id": known_staff,
                               "device_id": device.id, "device_name": device.name})
        if locked:
            raise AppError("pin_locked", 429)
        raise AppError("invalid_pin", 401)

    assert staff is not None
    await r.delete(f"pin:fail:staff:{staff_id}")
    device.last_seen_at = now()
    session.add(AuditLog(venue_id=device.venue_id, staff_id=staff.id, device_id=device.id, action="auth.pin_login",
                         entity="staff", entity_id=str(staff.id), data={}, ip=ip))
    return await issue_tokens(session, staff=staff, venue_id=device.venue_id, device_id=device.id)


# ---------- Вход владельца/админа (пароль + 2FA) ----------


async def password_login(
    session: AsyncSession, *, email: str | None, phone: str | None, password: str, totp_code: str | None,
    venue_id: uuid.UUID | None, ip: str | None,
) -> TokensOut:
    if not email and not phone:
        raise AppError("validation_error", 422, {"field": "email|phone"})
    cond = Staff.email == email.lower() if email else Staff.phone == phone
    candidates = list((await session.scalars(select(Staff).where(cond, Staff.is_active.is_(True)))).all())
    staff = next((c for c in candidates if verify_secret(c.password_hash, password)), None)
    if staff is None:
        raise AppError("invalid_credentials", 401)
    if staff.totp_secret:
        if not totp_code:
            raise AppError("totp_required", 401)
        if not pyotp.TOTP(staff.totp_secret).verify(totp_code, valid_window=1):
            raise AppError("invalid_totp", 401)
    target = venue_id or staff.venue_id
    if target != staff.venue_id:
        await ensure_owner_access(session, staff, target)
    session.add(AuditLog(venue_id=target, staff_id=staff.id, action="auth.password_login", entity="staff",
                         entity_id=str(staff.id), data={"totp": bool(staff.totp_secret)}, ip=ip))
    return await issue_tokens(session, staff=staff, venue_id=target, device_id=None)


async def ensure_owner_access(session: AsyncSession, staff: Staff, venue_id: uuid.UUID) -> None:
    venue = await session.get(Venue, venue_id)
    home = await session.get(Venue, staff.venue_id)
    if not (staff.is_owner and venue and home and venue.organization_id == home.organization_id):
        raise AppError("forbidden", 403)


async def totp_setup(ctx: Ctx) -> tuple[str, str]:
    secret = pyotp.random_base32()
    await get_redis().set(f"totp:pending:{ctx.staff_id}", secret, ex=600)
    staff = await ctx.session.get(Staff, ctx.staff_id)
    label = (staff.email or staff.phone or staff.name) if staff else str(ctx.staff_id)
    return secret, pyotp.TOTP(secret).provisioning_uri(name=label, issuer_name="Dostuk")


async def totp_confirm(ctx: Ctx, code: str) -> None:
    secret = await get_redis().get(f"totp:pending:{ctx.staff_id}")
    if not secret or not pyotp.TOTP(secret).verify(code, valid_window=1):
        raise AppError("invalid_totp", 400)
    await ctx.session.execute(update(Staff).where(Staff.id == ctx.staff_id).values(totp_secret=secret))
    await get_redis().delete(f"totp:pending:{ctx.staff_id}")
    await audit.record(ctx, "auth.totp_enabled", "staff", ctx.staff_id)


# ---------- Роли ----------


async def seed_default_roles(session: AsyncSession, venue_id: uuid.UUID) -> dict[str, Role]:
    roles: dict[str, Role] = {}
    for name, perms in DEFAULT_ROLES.items():
        role = Role(venue_id=venue_id, name=name, is_system=True)
        session.add(role)
        await session.flush()
        for p in perms:
            session.add(RolePermission(role_id=role.id, permission=p, venue_id=venue_id))
        roles[name] = role
    await session.flush()
    return roles


def _validate_perms(perms: list[str]) -> list[str]:
    unknown = [p for p in perms if p not in PERMISSIONS]
    if unknown:
        raise AppError("validation_error", 422, {"unknown_permissions": unknown})
    return sorted(set(perms))


async def role_permissions(session: AsyncSession, role_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    out: dict[uuid.UUID, list[str]] = {r: [] for r in role_ids}
    rows = await session.execute(select(RolePermission.role_id, RolePermission.permission).where(RolePermission.role_id.in_(role_ids)))
    for rid, perm in rows:
        out[rid].append(perm)
    return {k: sorted(v) for k, v in out.items()}


async def create_role(ctx: Ctx, name: str, perms: list[str]) -> Role:
    perms = _validate_perms(perms)
    role = Role(venue_id=ctx.venue_id, name=name)
    ctx.session.add(role)
    await ctx.session.flush()
    for p in perms:
        ctx.session.add(RolePermission(role_id=role.id, permission=p, venue_id=ctx.venue_id))
    await audit.record(ctx, "role.created", "role", role.id, name=name, permissions=perms)
    await ctx.session.flush()
    return role


async def set_role_permissions(ctx: Ctx, role_id: uuid.UUID, perms: list[str]) -> list[str]:
    perms = _validate_perms(perms)
    role = await ctx.session.get(Role, role_id)
    if role is None:
        raise NotFound("role")
    before = (await role_permissions(ctx.session, [role_id]))[role_id]
    await ctx.session.execute(delete(RolePermission).where(RolePermission.role_id == role_id))
    for p in perms:
        ctx.session.add(RolePermission(role_id=role_id, permission=p, venue_id=ctx.venue_id))
    await audit.record(ctx, "role.permissions_changed", "role", role_id, before=before, after=perms)
    await ctx.session.flush()
    return perms


async def delete_role(ctx: Ctx, role_id: uuid.UUID) -> None:
    role = await ctx.session.get(Role, role_id)
    if role is None:
        raise NotFound("role")
    in_use = await ctx.session.scalar(select(Staff.id).where(Staff.role_id == role_id).limit(1))
    if in_use or role.is_system:
        raise Conflict("conflict", reason="role_in_use" if in_use else "system_role")
    await ctx.session.delete(role)
    await audit.record(ctx, "role.deleted", "role", role_id, name=role.name)


# ---------- Сотрудники ----------


async def staff_stations(session: AsyncSession, staff_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[uuid.UUID]]:
    out: dict[uuid.UUID, list[uuid.UUID]] = {s: [] for s in staff_ids}
    rows = await session.execute(select(StaffStation.staff_id, StaffStation.station_id).where(StaffStation.staff_id.in_(staff_ids)))
    for sid, st in rows:
        out[sid].append(st)
    return out


async def _set_stations(ctx: Ctx, staff_id: uuid.UUID, station_ids: list[uuid.UUID]) -> None:
    await ctx.session.execute(delete(StaffStation).where(StaffStation.staff_id == staff_id))
    for st in set(station_ids):
        ctx.session.add(StaffStation(staff_id=staff_id, station_id=st, venue_id=ctx.venue_id))


async def create_staff(ctx: Ctx, data) -> Staff:
    if await ctx.session.get(Role, data.role_id) is None:
        raise NotFound("role")
    staff = Staff(
        id=data.id or uuid.uuid4(),
        venue_id=ctx.venue_id,
        name=data.name,
        role_id=data.role_id,
        pin_hash=hash_secret(data.pin) if data.pin else None,
        phone=data.phone,
        email=data.email.lower() if data.email else None,
        password_hash=hash_secret(data.password) if data.password else None,
    )
    ctx.session.add(staff)
    await ctx.session.flush()
    if data.station_ids:
        await _set_stations(ctx, staff.id, data.station_ids)
    await audit.record(ctx, "staff.created", "staff", staff.id, name=staff.name, role_id=data.role_id)
    await ctx.session.flush()
    return staff


async def update_staff(ctx: Ctx, staff_id: uuid.UUID, data) -> Staff:
    staff = await ctx.session.get(Staff, staff_id)
    if staff is None:
        raise NotFound("staff")
    changes = data.model_dump(exclude_unset=True)
    if "role_id" in changes and await ctx.session.get(Role, changes["role_id"]) is None:
        raise NotFound("role")
    if changes.get("email"):
        changes["email"] = changes["email"].lower()
    for k, v in changes.items():
        setattr(staff, k, v)
    if changes.get("is_active") is False:
        await ctx.session.execute(
            update(RefreshToken).where(RefreshToken.staff_id == staff_id, RefreshToken.revoked_at.is_(None)).values(revoked_at=now())
        )
        await rt.emit(ctx.session, ctx.venue_id, rt.SESSION_REVOKED, [rt.ch_user(staff_id)], {"staff_id": staff_id})
    await audit.record(ctx, "staff.updated", "staff", staff_id, **changes)
    await ctx.session.flush()
    return staff


async def set_staff_pin(ctx: Ctx, staff_id: uuid.UUID, pin: str) -> None:
    staff = await ctx.session.get(Staff, staff_id)
    if staff is None:
        raise NotFound("staff")
    staff.pin_hash = hash_secret(pin)
    await get_redis().delete(f"pin:lock:staff:{staff_id}", f"pin:fail:staff:{staff_id}")
    await audit.record(ctx, "staff.pin_changed", "staff", staff_id)


async def set_staff_stations(ctx: Ctx, staff_id: uuid.UUID, station_ids: list[uuid.UUID]) -> None:
    if await ctx.session.get(Staff, staff_id) is None:
        raise NotFound("staff")
    await _set_stations(ctx, staff_id, station_ids)
    await audit.record(ctx, "staff.stations_changed", "staff", staff_id, station_ids=station_ids)
    await ctx.session.flush()
