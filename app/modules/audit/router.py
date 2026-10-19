import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.core.deps import Ctx, require
from app.core.pagination import make_page, paginate_desc
from app.modules.audit.models import AuditLog

router = APIRouter(tags=["audit"])


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    staff_id: uuid.UUID | None
    device_id: uuid.UUID | None
    action: str
    entity: str | None
    entity_id: str | None
    data: dict
    ip: str | None
    created_at: datetime


class AuditPage(BaseModel):
    items: list[AuditOut]
    next_cursor: str | None = None


@router.get("/audit", response_model=AuditPage)
async def list_audit(
    action: str | None = Query(default=None, description="точное действие или префикс с '*', например order.*"),
    entity: str | None = None,
    entity_id: str | None = None,
    staff_id: uuid.UUID | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    ctx: Ctx = Depends(require("audit.view")),
):
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action.like(action[:-1] + "%") if action.endswith("*") else AuditLog.action == action)
    if entity:
        stmt = stmt.where(AuditLog.entity == entity)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if staff_id:
        stmt = stmt.where(AuditLog.staff_id == staff_id)
    stmt = paginate_desc(stmt, AuditLog.created_at, AuditLog.id, cursor, limit)
    rows, nxt = make_page(list((await ctx.session.scalars(stmt)).all()), limit)
    items = []
    for r in rows:
        o = AuditOut.model_validate({**{c: getattr(r, c) for c in AuditOut.model_fields if c != "ip"},
                                     "ip": str(r.ip) if r.ip else None})
        items.append(o)
    return AuditPage(items=items, next_cursor=nxt)
