import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class OrderItemIn(BaseModel):
    id: uuid.UUID | None = None  # клиент может создать UUID офлайн
    menu_item_id: uuid.UUID
    quantity: int = Field(default=1, ge=1, le=99)
    modifier_ids: list[uuid.UUID] = []
    comment: str | None = Field(default=None, max_length=500)
    course: int = Field(default=1, ge=1, le=5)


class OrderCreate(BaseModel):
    id: uuid.UUID | None = None
    table_id: uuid.UUID | None = None
    guests: int = Field(default=1, ge=1, le=100)
    comment: str | None = Field(default=None, max_length=1000)
    items: list[OrderItemIn] = []


class AddItemsIn(BaseModel):
    items: list[OrderItemIn] = Field(min_length=1)


ItemStatus = Literal["queued", "cooking", "ready", "served", "cancelled"]


class ItemStatusIn(BaseModel):
    status: ItemStatus
    reason: str | None = Field(default=None, max_length=500)


class TransferIn(BaseModel):
    table_id: uuid.UUID | None = None
    waiter_id: uuid.UUID | None = None


class CancelIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class OrderItemModifierOut(ORM):
    id: uuid.UUID
    modifier_id: uuid.UUID | None
    name: str
    price: int


class OrderItemOut(ORM):
    id: uuid.UUID
    order_id: uuid.UUID
    menu_item_id: uuid.UUID | None
    station_id: uuid.UUID | None
    name: str
    price: int
    quantity: int
    modifiers_total: int
    total: int
    status: str
    comment: str | None
    course: int
    created_by: uuid.UUID | None
    created_at: datetime
    cooking_at: datetime | None
    ready_at: datetime | None
    served_at: datetime | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    modifiers: list[OrderItemModifierOut] = []


class PaymentOut(ORM):
    id: uuid.UUID
    method: str
    amount: int
    tendered: int | None
    change: int
    created_by: uuid.UUID | None
    created_at: datetime
    refunded_at: datetime | None


class DiscountOut(ORM):
    id: uuid.UUID
    kind: str
    value: int
    reason: str | None
    created_by: uuid.UUID | None
    created_at: datetime


class OrderOut(ORM):
    id: uuid.UUID
    business_date: date
    number: int
    table_id: uuid.UUID | None
    waiter_id: uuid.UUID | None
    shift_id: uuid.UUID | None
    status: str
    source: str
    guests: int
    comment: str | None
    subtotal: int
    discount_total: int
    service_charge: int
    total: int
    paid_total: int
    remaining: int = 0
    opened_at: datetime
    billed_at: datetime | None
    closed_at: datetime | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    updated_at: datetime
    items: list[OrderItemOut] = []
    payments: list[PaymentOut] = []
    discounts: list[DiscountOut] = []


class OrderBrief(ORM):
    id: uuid.UUID
    business_date: date
    number: int
    table_id: uuid.UUID | None
    waiter_id: uuid.UUID | None
    status: str
    source: str
    guests: int
    total: int
    paid_total: int
    opened_at: datetime
    closed_at: datetime | None


class OrderPage(BaseModel):
    items: list[OrderBrief]
    next_cursor: str | None = None


class KitchenItem(OrderItemOut):
    order_number: int
    table_id: uuid.UUID | None
    table_name: str | None
    waiter_id: uuid.UUID | None
    order_comment: str | None
