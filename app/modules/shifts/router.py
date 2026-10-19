import uuid
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.core.deps import Ctx, require
from app.core.errors import NotFound
from app.core.pagination import make_page, paginate_desc
from app.modules.shifts import service
from app.modules.shifts.models import CashMovement, Shift

router = APIRouter(prefix="/shifts", tags=["shifts"])


class ShiftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    number: int
    business_date: date
    status: str
    opened_by: uuid.UUID | None
    closed_by: uuid.UUID | None
    opened_at: datetime
    closed_at: datetime | None
    opening_cash: int
    expected_cash: int | None
    counted_cash: int | None
    z_report: dict | None
    comment: str | None


class OpenIn(BaseModel):
    opening_cash: int | None = Field(default=None, ge=0)
    comment: str | None = None


class CloseIn(BaseModel):
    counted_cash: int = Field(ge=0)
    comment: str | None = None
    force: bool = False  # закрыть, даже если есть открытые заказы (они перейдут в следующую смену)


class CashIn(BaseModel):
    kind: Literal["expense", "deposit", "collection"] = "expense"
    amount: int = Field(gt=0)
    comment: str | None = Field(default=None, max_length=500)


class CashOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    shift_id: uuid.UUID
    kind: str
    amount: int
    comment: str | None
    created_by: uuid.UUID | None
    created_at: datetime


class ShiftPage(BaseModel):
    items: list[ShiftOut]
    next_cursor: str | None = None


def _out(shift: Shift) -> ShiftOut:
    return ShiftOut.model_validate(shift)


@router.post("/open", response_model=ShiftOut, status_code=201)
async def open_shift(body: OpenIn, ctx: Ctx = Depends(require("shifts.open"))):
    return _out(await service.open_shift(ctx, body.opening_cash, body.comment))


@router.get("/current", response_model=ShiftOut | None)
async def current_shift(ctx: Ctx = Depends(require())):
    shift = await service.current(ctx.session)
    return _out(shift) if shift else None


@router.get("/current/x-report")
async def x_report(print: bool = False, ctx: Ctx = Depends(require("shifts.close"))):
    return await service.x_report(ctx, print)


@router.post("/close", response_model=ShiftOut)
async def close_shift(body: CloseIn, ctx: Ctx = Depends(require("shifts.close"))):
    return _out(await service.close_shift(ctx, body.counted_cash, body.comment, force=body.force))


@router.get("", response_model=ShiftPage)
async def list_shifts(cursor: str | None = None, limit: int = Query(default=30, ge=1, le=100),
                      ctx: Ctx = Depends(require("reports.view"))):
    stmt = paginate_desc(select(Shift), Shift.opened_at, Shift.id, cursor, limit)
    rows, nxt = make_page(list((await ctx.session.scalars(stmt)).all()), limit, ts_attr="opened_at")
    return ShiftPage(items=[_out(r) for r in rows], next_cursor=nxt)


@router.post("/expenses", response_model=CashOut, status_code=201)
async def add_cash(body: CashIn, ctx: Ctx = Depends(require("shifts.cash"))):
    """Расход, внесение или инкассация в текущей смене."""
    return await service.add_cash_movement(ctx, body.kind, body.amount, body.comment)


@router.get("/expenses", response_model=list[CashOut])
async def list_cash(shift_id: uuid.UUID | None = None, ctx: Ctx = Depends(require("shifts.cash"))):
    if shift_id is None:
        shift = await service.require_open(ctx.session)
        shift_id = shift.id
    rows = await ctx.session.scalars(select(CashMovement).where(CashMovement.shift_id == shift_id).order_by(CashMovement.created_at))
    return list(rows.all())


@router.get("/{shift_id}", response_model=ShiftOut)
async def get_shift(shift_id: uuid.UUID, ctx: Ctx = Depends(require("reports.view"))):
    shift = await ctx.session.get(Shift, shift_id)
    if shift is None:
        raise NotFound("shift")
    return _out(shift)
