import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.deps import Ctx, require
from app.modules.billing import service
from app.modules.orders.schemas import OrderOut
from app.modules.orders.service import order_out

router = APIRouter(tags=["billing"])


class BillIn(BaseModel):
    print: bool = True


class PaymentIn(BaseModel):
    id: uuid.UUID | None = None
    method: Literal["cash", "card", "qr", "other"]
    amount: int = Field(gt=0)
    tendered: int | None = Field(default=None, gt=0)  # сколько дал гость наличными


class DiscountIn(BaseModel):
    kind: Literal["percent", "amount"]
    value: int = Field(ge=0)  # percent — базисные пункты (1000 = 10%), amount — тыйыны
    reason: str | None = Field(default=None, max_length=300)


class RefundIn(BaseModel):
    reason: str = Field(min_length=1, max_length=300)


@router.post("/orders/{order_id}/bill", response_model=OrderOut)
async def bill(order_id: uuid.UUID, body: BillIn | None = None, ctx: Ctx = Depends(require("orders.create"))):
    order = await service.bill(ctx, order_id, print_it=body.print if body else True)
    return await order_out(ctx.session, order)


@router.post("/orders/{order_id}/payments", response_model=OrderOut, status_code=201)
async def add_payment(order_id: uuid.UUID, body: PaymentIn, ctx: Ctx = Depends(require("billing.pay"))):
    """Несколько оплат на заказ: часть наличными, часть картой. Заказ закрывается, когда оплачен полностью."""
    order, _ = await service.add_payment(ctx, order_id, body.method, body.amount, body.tendered, body.id)
    return await order_out(ctx.session, order)


@router.post("/orders/{order_id}/discount", response_model=OrderOut)
async def discount(order_id: uuid.UUID, body: DiscountIn, ctx: Ctx = Depends(require("billing.discount"))):
    order = await service.add_discount(ctx, order_id, body.kind, body.value, body.reason)
    return await order_out(ctx.session, order)


@router.post("/payments/{payment_id}/refund", response_model=OrderOut)
async def refund(payment_id: uuid.UUID, body: RefundIn, ctx: Ctx = Depends(require("billing.refund"))):
    order = await service.refund_payment(ctx, payment_id, body.reason)
    return await order_out(ctx.session, order)
