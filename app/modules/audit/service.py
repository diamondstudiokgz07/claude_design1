from typing import TYPE_CHECKING, Any

from app.modules.audit.models import AuditLog
from app.modules.realtime.service import _jsonable

if TYPE_CHECKING:
    from app.core.deps import Ctx


async def record(ctx: "Ctx", action: str, entity: str | None = None, entity_id: Any = None, **data: Any) -> None:
    ctx.session.add(
        AuditLog(
            venue_id=ctx.venue_id,
            staff_id=ctx.staff_id,
            device_id=ctx.device_id,
            action=action,
            entity=entity,
            entity_id=str(entity_id) if entity_id is not None else None,
            data=_jsonable(data),
            ip=ctx.ip,
        )
    )
