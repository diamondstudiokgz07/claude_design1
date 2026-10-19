import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Ctx
from app.core.errors import AppError, NotFound
from app.core.security import now
from app.modules.printing.models import Printer, PrintJob
from app.modules.realtime import service as rt


async def default_receipt_printer(session: AsyncSession) -> uuid.UUID | None:
    return await session.scalar(
        select(Printer.id).where(Printer.is_active.is_(True), Printer.kind == "receipt")
        .order_by(Printer.is_default_receipt.desc(), Printer.created_at).limit(1)
    )


async def create_job(
    session: AsyncSession, venue_id: uuid.UUID, kind: str, payload: dict[str, Any], printer_id: uuid.UUID | None = None
) -> PrintJob:
    """Касса получает PRINT_JOB, печатает ESC/POS на нужный принтер и подтверждает задание."""
    if printer_id is None and kind != "kitchen_ticket":
        printer_id = await default_receipt_printer(session)
    job = PrintJob(venue_id=venue_id, printer_id=printer_id, kind=kind, payload=rt._jsonable(payload))
    session.add(job)
    await session.flush()
    await rt.emit(session, venue_id, rt.PRINT_JOB, [rt.ch_admin(venue_id)],
                  {"id": job.id, "kind": kind, "printer_id": printer_id})
    return job


async def ack_job(ctx: Ctx, job_id: uuid.UUID, ok: bool, error: str | None) -> PrintJob:
    job = await ctx.session.get(PrintJob, job_id, with_for_update=True)
    if job is None:
        raise NotFound("print_job")
    if job.status == "done":
        return job
    job.attempts += 1
    if ok:
        job.status = "done"
        job.printed_at = now()
        job.error = None
    else:
        job.status = "failed"
        job.error = error
    await ctx.session.flush()
    return job


async def retry_job(ctx: Ctx, job_id: uuid.UUID) -> PrintJob:
    job = await ctx.session.get(PrintJob, job_id)
    if job is None:
        raise NotFound("print_job")
    if job.status == "done":
        raise AppError("conflict", 409, {"reason": "already_printed"})
    job.status = "pending"
    await rt.emit(ctx.session, ctx.venue_id, rt.PRINT_JOB, [rt.ch_admin(ctx.venue_id)],
                  {"id": job.id, "kind": job.kind, "printer_id": job.printer_id, "retry": True})
    await ctx.session.flush()
    return job
