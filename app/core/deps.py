"""Контекст запроса: сессия БД с RLS, сотрудник, заведение (из токена), права."""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, Forbidden
from app.core.permissions import PERMISSIONS
from app.core.security import decode_access_token
from app.db import get_sessionmaker, set_tenant


@dataclass
class Ctx:
    session: AsyncSession
    venue_id: uuid.UUID
    staff_id: uuid.UUID
    staff_name: str
    role_id: uuid.UUID
    session_id: uuid.UUID
    device_id: uuid.UUID | None
    device_kind: str | None
    is_owner: bool
    permissions: set[str] = field(default_factory=set)
    station_ids: list[uuid.UUID] = field(default_factory=list)  # пусто = все станции
    ip: str | None = None
    lang: str = "ru"

    def has(self, perm: str) -> bool:
        return perm in self.permissions

    def require(self, perm: str) -> None:
        if perm not in self.permissions:
            raise Forbidden(permission=perm)


def client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


async def db_system() -> AsyncIterator[AsyncSession]:
    """Сессия без привязки к заведению (обходит RLS) — только для входа и публичного API."""
    async with get_sessionmaker()() as session:
        async with session.begin():
            await set_tenant(session, None, bypass=True)
            yield session


def _bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise AppError("unauthorized", 401)
    return auth[7:].strip()


async def load_ctx(session: AsyncSession, token: str, request: Request | None = None) -> Ctx:
    from app.modules.identity.models import (
        Device,
        RefreshToken,
        RolePermission,
        Staff,
        StaffStation,
    )
    from app.modules.venues.models import Venue

    claims = decode_access_token(token)
    staff_id = uuid.UUID(claims["sub"])
    venue_id = uuid.UUID(claims["vid"])
    session_id = uuid.UUID(claims["sid"])
    device_id = uuid.UUID(claims["did"]) if claims.get("did") else None

    # Проверки выполняем без RLS (владелец может работать в «чужом» заведении своей сети),
    # затем включаем RLS на заведение из токена.
    await set_tenant(session, None, bypass=True)
    staff = await session.get(Staff, staff_id)
    if staff is None or not staff.is_active:
        raise AppError("unauthorized", 401)
    rt = await session.get(RefreshToken, session_id)
    if rt is None or rt.revoked_at is not None:
        raise AppError("unauthorized", 401, {"reason": "session_revoked"})
    device_kind = None
    if device_id:
        device = await session.get(Device, device_id)
        if device is None or device.revoked_at is not None:
            raise AppError("device_revoked", 401)
        device_kind = device.kind
    if staff.venue_id != venue_id:
        venue = await session.get(Venue, venue_id)
        home = await session.get(Venue, staff.venue_id)
        if not (staff.is_owner and venue and home and venue.organization_id == home.organization_id):
            raise AppError("unauthorized", 401)

    if staff.is_owner:
        perms = set(PERMISSIONS)
    else:
        perms = set(
            (await session.scalars(select(RolePermission.permission).where(RolePermission.role_id == staff.role_id))).all()
        )
    stations = list(
        (await session.scalars(select(StaffStation.station_id).where(StaffStation.staff_id == staff.id))).all()
    )

    await set_tenant(session, venue_id)
    from app.core.errors import pick_lang

    return Ctx(
        session=session,
        venue_id=venue_id,
        staff_id=staff.id,
        staff_name=staff.name,
        role_id=staff.role_id,
        session_id=session_id,
        device_id=device_id,
        device_kind=device_kind,
        is_owner=staff.is_owner,
        permissions=perms,
        station_ids=stations,
        ip=client_ip(request) if request else None,
        lang=pick_lang(request.headers.get("accept-language")) if request else "ru",
    )


async def current_ctx(request: Request) -> AsyncIterator[Ctx]:
    token = _bearer(request)
    async with get_sessionmaker()() as session:
        async with session.begin():
            yield await load_ctx(session, token, request)


def require(*perms: str):
    """Зависимость: требует все перечисленные права. Проверка только на сервере."""

    async def _dep(ctx: Ctx = Depends(current_ctx)) -> Ctx:
        for p in perms:
            ctx.require(p)
        return ctx

    return _dep
