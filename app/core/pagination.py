"""Курсорная пагинация по (created_at, id)."""

import base64
import json
import uuid
from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel
from sqlalchemy import Select, and_, or_

from app.core.errors import AppError

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = None


def encode_cursor(ts: datetime, id_: Any) -> str:
    raw = json.dumps([ts.isoformat(), str(id_)]).encode()
    return base64.urlsafe_b64encode(raw).decode()


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        ts, id_ = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return datetime.fromisoformat(ts), id_
    except Exception as e:  # noqa: BLE001
        raise AppError("validation_error", 422, {"cursor": "invalid"}) from e


def paginate_desc(stmt: Select, ts_col, id_col, cursor: str | None, limit: int) -> Select:
    """Сортировка от новых к старым."""
    if cursor:
        ts, id_ = decode_cursor(cursor)
        id_val: Any = int(id_) if id_.isdigit() else uuid.UUID(id_)
        stmt = stmt.where(or_(ts_col < ts, and_(ts_col == ts, id_col < id_val)))
    return stmt.order_by(ts_col.desc(), id_col.desc()).limit(limit + 1)


def make_page(rows: list, limit: int, ts_attr: str = "created_at", id_attr: str = "id") -> tuple[list, str | None]:
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        return rows, encode_cursor(getattr(last, ts_attr), getattr(last, id_attr))
    return rows, None
