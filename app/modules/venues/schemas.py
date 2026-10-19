import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class VenueOut(ORM):
    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    slug: str
    timezone: str
    currency: str
    languages: list[str]
    address: str | None
    phone: str | None
    working_hours: dict
    is_active: bool


class VenueCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    timezone: str = "Asia/Bishkek"
    address: str | None = None
    phone: str | None = None


class VenueUpdate(BaseModel):
    name: str | None = None
    timezone: str | None = None
    languages: list[str] | None = None
    address: str | None = None
    phone: str | None = None
    working_hours: dict | None = None


class SettingsOut(ORM):
    receipt_header: str | None
    receipt_footer: str | None
    legal_name: str | None
    tax_id: str | None
    service_charge_bp: int
    business_day_start_hour: int
    auto_close_shift: bool
    min_app_version: str | None
    max_discount_bp: int
    default_opening_cash: int
    extra: dict


class SettingsUpdate(BaseModel):
    receipt_header: str | None = None
    receipt_footer: str | None = None
    legal_name: str | None = None
    tax_id: str | None = None
    service_charge_bp: int | None = Field(default=None, ge=0, le=5000)
    business_day_start_hour: int | None = Field(default=None, ge=0, le=23)
    auto_close_shift: bool | None = None
    min_app_version: str | None = None
    max_discount_bp: int | None = Field(default=None, ge=0, le=10000)
    default_opening_cash: int | None = Field(default=None, ge=0)
    extra: dict | None = None


class HallOut(ORM):
    id: uuid.UUID
    name: str
    sort_order: int
    is_active: bool


class HallIn(BaseModel):
    id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=100)
    sort_order: int = 0
    is_active: bool = True


class HallUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    sort_order: int | None = None
    is_active: bool | None = None


class TableOut(ORM):
    id: uuid.UUID
    hall_id: uuid.UUID
    name: str
    seats: int
    qr_token: str
    sort_order: int
    is_active: bool


class TableState(TableOut):
    open_order_id: uuid.UUID | None = None
    open_order_status: str | None = None
    open_order_total: int | None = None
    waiter_id: uuid.UUID | None = None


class TableIn(BaseModel):
    id: uuid.UUID | None = None
    hall_id: uuid.UUID
    name: str = Field(min_length=1, max_length=50)
    seats: int = Field(default=4, ge=1, le=100)
    sort_order: int = 0


class TableUpdate(BaseModel):
    hall_id: uuid.UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=50)
    seats: int | None = Field(default=None, ge=1, le=100)
    sort_order: int | None = None
    is_active: bool | None = None


class BootstrapIn(BaseModel):
    organization: str
    venue: VenueCreate
    owner_name: str
    owner_email: EmailStr
    owner_password: str = Field(min_length=8)
