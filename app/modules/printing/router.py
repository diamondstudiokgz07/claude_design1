import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.core.deps import Ctx, require
from app.core.errors import NotFound
from app.modules.audit import service as audit
from app.modules.printing import service
from app.modules.printing.models import Printer, PrintJob

router = APIRouter(tags=["printing"])


class PrinterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    kind: str
    connection: str
    address: str | None
    paper_width: int
    is_default_receipt: bool
    is_active: bool


class PrinterIn(BaseModel):
    id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=80)
    kind: str = Field(default="kitchen", pattern="^(receipt|kitchen)$")
    connection: str = Field(default="network", pattern="^(network|usb)$")
    address: str | None = None
    paper_width: int = Field(default=80, ge=58, le=80)
    is_default_receipt: bool = False
    is_active: bool = True


class PrintJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    printer_id: uuid.UUID | None
    kind: str
    payload: dict
    status: str
    attempts: int
    error: str | None
    created_at: datetime
    printed_at: datetime | None


class AckIn(BaseModel):
    ok: bool
    error: str | None = None


@router.get("/printers", response_model=list[PrinterOut])
async def list_printers(ctx: Ctx = Depends(require("printing.manage"))):
    return list((await ctx.session.scalars(select(Printer).order_by(Printer.name))).all())


@router.post("/printers", response_model=PrinterOut, status_code=201)
async def create_printer(body: PrinterIn, ctx: Ctx = Depends(require("printing.manage"))):
    p = Printer(venue_id=ctx.venue_id, **body.model_dump(exclude={"id"}))
    if body.id:
        p.id = body.id
    ctx.session.add(p)
    await ctx.session.flush()
    await audit.record(ctx, "printer.created", "printer", p.id, name=p.name)
    return p


@router.put("/printers/{printer_id}", response_model=PrinterOut)
async def update_printer(printer_id: uuid.UUID, body: PrinterIn, ctx: Ctx = Depends(require("printing.manage"))):
    p = await ctx.session.get(Printer, printer_id)
    if p is None:
        raise NotFound("printer")
    for k, v in body.model_dump(exclude={"id"}).items():
        setattr(p, k, v)
    await audit.record(ctx, "printer.updated", "printer", p.id)
    return p


@router.get("/print-jobs", response_model=list[PrintJobOut])
async def list_jobs(status: str = "pending", limit: int = 100, ctx: Ctx = Depends(require("printing.manage"))):
    """Касса после старта/переподключения забирает невыполненные задания."""
    stmt = select(PrintJob).order_by(PrintJob.created_at).limit(min(limit, 500))
    if status != "all":
        stmt = stmt.where(PrintJob.status == status)
    return list((await ctx.session.scalars(stmt)).all())


@router.post("/print-jobs/{job_id}/ack", response_model=PrintJobOut)
async def ack_job(job_id: uuid.UUID, body: AckIn, ctx: Ctx = Depends(require("printing.manage"))):
    return await service.ack_job(ctx, job_id, body.ok, body.error)


@router.post("/print-jobs/{job_id}/retry", response_model=PrintJobOut)
async def retry_job(job_id: uuid.UUID, ctx: Ctx = Depends(require("printing.manage"))):
    return await service.retry_job(ctx, job_id)
