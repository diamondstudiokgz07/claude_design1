from datetime import date

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from app.core.deps import Ctx, require
from app.core.errors import AppError
from app.modules.reports import service

router = APIRouter(prefix="/reports", tags=["reports"])


def _check(date_from: date, date_to: date) -> None:
    if date_from > date_to or (date_to - date_from).days > 366:
        raise AppError("validation_error", 422, {"field": "from/to", "reason": "max 366 days"})


@router.get("/sales")
async def sales(date_from: date, date_to: date, ctx: Ctx = Depends(require("reports.view"))):
    _check(date_from, date_to)
    return await service.sales(ctx.session, date_from, date_to)


@router.get("/sales.xlsx")
async def sales_xlsx(date_from: date, date_to: date, ctx: Ctx = Depends(require("reports.view"))):
    _check(date_from, date_to)
    data = await service.sales(ctx.session, date_from, date_to)
    return Response(
        service.sales_xlsx(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="sales_{date_from}_{date_to}.xlsx"'},
    )
