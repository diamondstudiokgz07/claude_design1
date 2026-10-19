import uuid

from pydantic import BaseModel, ConfigDict, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CategoryOut(ORM):
    id: uuid.UUID
    name: str
    translations: dict
    parent_id: uuid.UUID | None
    default_station_id: uuid.UUID | None
    sort_order: int
    is_active: bool


class CategoryIn(BaseModel):
    id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=120)
    translations: dict[str, str] = {}
    parent_id: uuid.UUID | None = None
    default_station_id: uuid.UUID | None = None
    sort_order: int = 0
    is_active: bool = True


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    translations: dict[str, str] | None = None
    parent_id: uuid.UUID | None = None
    default_station_id: uuid.UUID | None = None
    sort_order: int | None = None
    is_active: bool | None = None


class TranslationIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None


class ItemOut(ORM):
    id: uuid.UUID
    category_id: uuid.UUID
    station_id: uuid.UUID
    name: str
    description: str | None
    price: int
    weight_grams: int | None
    cook_time_min: int | None
    photo_url: str | None = None
    photo_thumb_url: str | None = None
    is_available: bool
    available_now: bool = True  # с учётом выключенной станции
    is_active: bool
    sort_order: int
    translations: dict[str, TranslationIn] = {}
    modifier_group_ids: list[uuid.UUID] = []


class ItemIn(BaseModel):
    id: uuid.UUID | None = None
    category_id: uuid.UUID
    station_id: uuid.UUID | None = None  # по умолчанию — станция категории
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    price: int = Field(ge=0)
    weight_grams: int | None = Field(default=None, ge=0)
    cook_time_min: int | None = Field(default=None, ge=0)
    is_active: bool = True
    sort_order: int = 0
    translations: dict[str, TranslationIn] = {}
    modifier_group_ids: list[uuid.UUID] = []


class ItemUpdate(BaseModel):
    category_id: uuid.UUID | None = None
    station_id: uuid.UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = None
    price: int | None = Field(default=None, ge=0)
    weight_grams: int | None = None
    cook_time_min: int | None = None
    is_active: bool | None = None
    sort_order: int | None = None
    translations: dict[str, TranslationIn] | None = None
    modifier_group_ids: list[uuid.UUID] | None = None


class StopIn(BaseModel):
    is_available: bool


class ModifierOut(ORM):
    id: uuid.UUID
    group_id: uuid.UUID
    name: str
    translations: dict
    price: int
    is_available: bool
    sort_order: int


class ModifierIn(BaseModel):
    id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=120)
    translations: dict[str, str] = {}
    price: int = Field(default=0, ge=0)
    is_available: bool = True
    sort_order: int = 0


class ModifierGroupOut(ORM):
    id: uuid.UUID
    name: str
    translations: dict
    min_select: int
    max_select: int
    sort_order: int
    modifiers: list[ModifierOut] = []


class ModifierGroupIn(BaseModel):
    id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=120)
    translations: dict[str, str] = {}
    min_select: int = Field(default=0, ge=0)
    max_select: int = Field(default=1, ge=1)
    sort_order: int = 0
    modifiers: list[ModifierIn] = []
