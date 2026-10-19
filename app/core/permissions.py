"""Каталог прав. Роль = набор этих строк; каждый эндпоинт объявляет нужное право."""

PERMISSIONS: dict[str, str] = {
    "orders.view": "Видеть заказы",
    "orders.view_all": "Видеть заказы всех официантов",
    "orders.create": "Создавать заказы и добавлять позиции",
    "orders.transfer": "Переносить заказ на другой стол/официанта",
    "orders.cancel": "Отменять заказы и позиции после отправки",
    "kitchen.work": "Менять статусы блюд на кухне",
    "billing.pay": "Принимать оплату",
    "billing.discount": "Давать скидки",
    "billing.refund": "Возвраты",
    "shifts.open": "Открывать смену",
    "shifts.close": "Закрывать смену и Z-отчёт",
    "shifts.cash": "Расходы, внесения, инкассации",
    "menu.view": "Видеть меню",
    "menu.edit": "Редактировать меню",
    "menu.stop": "Управлять стоп-листом",
    "stations.manage": "Управлять станциями",
    "venue.manage": "Залы, столы, настройки заведения",
    "staff.manage": "Сотрудники и роли",
    "devices.manage": "Привязка и отзыв устройств",
    "printing.manage": "Принтеры и очередь печати",
    "reports.view": "Отчёты и выгрузки",
    "audit.view": "Журнал действий",
}

DEFAULT_ROLES: dict[str, list[str]] = {
    "admin": list(PERMISSIONS),
    "cashier": [
        "orders.view", "orders.view_all", "orders.create", "orders.transfer", "billing.pay",
        "billing.discount", "shifts.open", "shifts.close", "shifts.cash", "menu.view", "menu.stop",
        "printing.manage", "reports.view",
    ],
    "waiter": ["orders.view", "orders.create", "menu.view"],
    "cook": ["kitchen.work", "menu.view", "menu.stop"],
}
