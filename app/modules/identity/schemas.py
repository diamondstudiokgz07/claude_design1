import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

DeviceKind = Literal["waiter", "kitchen", "pos"]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PairingCodeIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: DeviceKind


class PairingCodeOut(BaseModel):
    device_id: uuid.UUID
    code: str
    expires_at: datetime


class PairIn(BaseModel):
    code: str = Field(min_length=6, max_length=16)
    platform: str | None = None
    app_version: str | None = None


class VenueBrief(ORM):
    id: uuid.UUID
    name: str
    slug: str


class DeviceOut(ORM):
    id: uuid.UUID
    name: str
    kind: str
    platform: str | None
    app_version: str | None
    paired_at: datetime | None
    last_seen_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class PairOut(BaseModel):
    device_token: str
    device: DeviceOut
    venue: VenueBrief


class StaffPick(ORM):
    id: uuid.UUID
    name: str
    role_id: uuid.UUID


class PinIn(BaseModel):
    staff_id: uuid.UUID
    pin: str = Field(min_length=4, max_length=8, pattern=r"^\d+$")


class LoginIn(BaseModel):
    email: EmailStr | None = None
    phone: str | None = None
    password: str
    totp_code: str | None = None
    venue_id: uuid.UUID | None = None


class RefreshIn(BaseModel):
    refresh_token: str


class SwitchVenueIn(BaseModel):
    venue_id: uuid.UUID


class TokensOut(BaseModel):
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime
    token_type: str = "bearer"


class MeOut(BaseModel):
    staff: StaffPick
    is_owner: bool
    venue: VenueBrief
    device: DeviceOut | None
    permissions: list[str]
    station_ids: list[uuid.UUID]
    min_app_version: str


class TotpSetupOut(BaseModel):
    secret: str
    otpauth_uri: str


class TotpConfirmIn(BaseModel):
    code: str


# --- Сотрудники и роли ---


class StaffOut(ORM):
    id: uuid.UUID
    name: str
    role_id: uuid.UUID
    phone: str | None
    email: str | None
    is_owner: bool
    is_active: bool
    has_pin: bool = False
    station_ids: list[uuid.UUID] = []
    created_at: datetime


class StaffCreate(BaseModel):
    id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=120)
    role_id: uuid.UUID
    pin: str | None = Field(default=None, min_length=4, max_length=8, pattern=r"^\d+$")
    phone: str | None = None
    email: EmailStr | None = None
    password: str | None = Field(default=None, min_length=8)
    station_ids: list[uuid.UUID] = []


class StaffUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    role_id: uuid.UUID | None = None
    phone: str | None = None
    email: EmailStr | None = None
    is_active: bool | None = None


class StaffPinIn(BaseModel):
    pin: str = Field(min_length=4, max_length=8, pattern=r"^\d+$")


class StaffStationsIn(BaseModel):
    station_ids: list[uuid.UUID]


class RoleOut(ORM):
    id: uuid.UUID
    name: str
    is_system: bool
    permissions: list[str] = []


class RoleIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    permissions: list[str] = []


class RolePermissionsIn(BaseModel):
    permissions: list[str]


class PermissionOut(BaseModel):
    code: str
    title: str
