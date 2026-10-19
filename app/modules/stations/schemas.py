import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class StationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    color: str
    sort_order: int
    printer_id: uuid.UUID | None
    is_active: bool
    archived_at: datetime | None
    items_count: int = 0


class StationIn(BaseModel):
    id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=80)
    color: str = Field(default="#FF7A00", pattern=r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")
    sort_order: int = 0
    printer_id: uuid.UUID | None = None


class StationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")
    sort_order: int | None = None
    printer_id: uuid.UUID | None = None
    is_active: bool | None = None  # false = выключить временно (блюда уходят в стоп-лист)


class SplitIn(BaseModel):
    """Из станции выделяется новая; переезжают отмеченные блюда и/или целые категории."""

    new_station: StationIn
    item_ids: list[uuid.UUID] = []
    category_ids: list[uuid.UUID] = []
    move_open_items: bool = True  # незакрытые позиции переехавших блюд тоже переходят


class MergeIn(BaseModel):
    """Все блюда этой станции переносятся в target, эта станция архивируется."""

    target_id: uuid.UUID


class StationOpResult(BaseModel):
    station: StationOut
    moved_items: int = 0
    moved_open_order_items: int = 0
    moved_categories: int = 0
