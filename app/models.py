"""Импорт всех моделей — для Alembic и для разрешения связей."""

from app.core.models import File, IdempotencyKey  # noqa: F401
from app.db import Base
from app.modules.audit.models import AuditLog  # noqa: F401
from app.modules.billing.models import Discount, FiscalReceipt, Payment  # noqa: F401
from app.modules.identity.models import (  # noqa: F401
    Device,
    RefreshToken,
    Role,
    RolePermission,
    Staff,
    StaffStation,
)
from app.modules.menu.models import (  # noqa: F401
    Category,
    MenuItem,
    MenuItemModifierGroup,
    MenuItemTranslation,
    Modifier,
    ModifierGroup,
)
from app.modules.orders.models import Order, OrderCounter, OrderItem, OrderItemModifier  # noqa: F401
from app.modules.printing.models import Printer, PrintJob  # noqa: F401
from app.modules.realtime.models import OutboxEvent  # noqa: F401
from app.modules.shifts.models import CashMovement, Shift  # noqa: F401
from app.modules.stations.models import Station  # noqa: F401
from app.modules.venues.models import Hall, Organization, Table, Venue, VenueSettings  # noqa: F401

# Таблицы с RLS (у всех есть venue_id). venues фильтруется по id.
TENANT_TABLES = sorted(
    t.name for t in Base.metadata.sorted_tables if "venue_id" in t.c and t.name != "venue_settings"
) + ["venue_settings"]
