"""Аналитика продаж и выгрузка в Excel."""

import io
from datetime import date
from typing import Any

from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.billing.models import Payment
from app.modules.identity.models import Staff
from app.modules.orders.models import ITEM_CANCELLED, ORDER_PAID, Order, OrderItem
from app.modules.stations.models import Station


async def sales(session: AsyncSession, date_from: date, date_to: date) -> dict[str, Any]:
    paid = (Order.status == ORDER_PAID, Order.business_date >= date_from, Order.business_date <= date_to)

    by_day = [
        {"date": d.isoformat(), "orders": int(n), "revenue": int(r), "guests": int(g)}
        for d, n, r, g in (await session.execute(
            select(Order.business_date, func.count(), func.sum(Order.total), func.sum(Order.guests))
            .where(*paid).group_by(Order.business_date).order_by(Order.business_date))).all()
    ]
    by_item = [
        {"menu_item_id": str(mid) if mid else None, "name": name, "quantity": int(q), "revenue": int(r)}
        for mid, name, q, r in (await session.execute(
            select(OrderItem.menu_item_id, OrderItem.name, func.sum(OrderItem.quantity), func.sum(OrderItem.total))
            .join(Order, Order.id == OrderItem.order_id)
            .where(*paid, OrderItem.status != ITEM_CANCELLED)
            .group_by(OrderItem.menu_item_id, OrderItem.name)
            .order_by(func.sum(OrderItem.total).desc()))).all()
    ]
    by_station = [
        {"station_id": str(sid) if sid else None, "name": name, "quantity": int(q), "revenue": int(r)}
        for sid, name, q, r in (await session.execute(
            select(OrderItem.station_id, Station.name, func.sum(OrderItem.quantity), func.sum(OrderItem.total))
            .join(Order, Order.id == OrderItem.order_id)
            .outerjoin(Station, Station.id == OrderItem.station_id)
            .where(*paid, OrderItem.status != ITEM_CANCELLED)
            .group_by(OrderItem.station_id, Station.name))).all()
    ]
    by_waiter = [
        {"waiter_id": str(wid) if wid else None, "name": name, "orders": int(n), "revenue": int(r)}
        for wid, name, n, r in (await session.execute(
            select(Order.waiter_id, Staff.name, func.count(), func.sum(Order.total))
            .outerjoin(Staff, Staff.id == Order.waiter_id)
            .where(*paid).group_by(Order.waiter_id, Staff.name)
            .order_by(func.sum(Order.total).desc()))).all()
    ]
    by_method = {
        m: int(a) for m, a in (await session.execute(
            select(Payment.method, func.sum(Payment.amount))
            .join(Order, Order.id == Payment.order_id)
            .where(*paid, Payment.refunded_at.is_(None)).group_by(Payment.method))).all()
    }
    total_orders = sum(d["orders"] for d in by_day)
    revenue = sum(d["revenue"] for d in by_day)
    return {
        "from": date_from.isoformat(), "to": date_to.isoformat(),
        "orders": total_orders, "revenue": revenue, "average_check": revenue // total_orders if total_orders else 0,
        "by_day": by_day, "by_item": by_item, "by_station": by_station, "by_waiter": by_waiter, "by_method": by_method,
    }


def _money(v: int) -> float:
    return round(v / 100, 2)


def sales_xlsx(data: dict[str, Any]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Итоги"
    ws.append(["Период", f'{data["from"]} — {data["to"]}'])
    ws.append(["Заказов", data["orders"]])
    ws.append(["Выручка, сом", _money(data["revenue"])])
    ws.append(["Средний чек, сом", _money(data["average_check"])])
    ws.append([])
    ws.append(["Способ оплаты", "Сумма, сом"])
    for m, a in data["by_method"].items():
        ws.append([m, _money(a)])

    sheets = [
        ("По дням", ["Дата", "Заказов", "Гостей", "Выручка, сом"],
         [[d["date"], d["orders"], d["guests"], _money(d["revenue"])] for d in data["by_day"]]),
        ("По блюдам", ["Блюдо", "Количество", "Выручка, сом"],
         [[i["name"], i["quantity"], _money(i["revenue"])] for i in data["by_item"]]),
        ("По станциям", ["Станция", "Количество", "Выручка, сом"],
         [[i["name"] or "—", i["quantity"], _money(i["revenue"])] for i in data["by_station"]]),
        ("По официантам", ["Официант", "Заказов", "Выручка, сом"],
         [[w["name"] or "—", w["orders"], _money(w["revenue"])] for w in data["by_waiter"]]),
    ]
    for title, header, rows in sheets:
        sh = wb.create_sheet(title)
        sh.append(header)
        for r in rows:
            sh.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
