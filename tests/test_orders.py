import uuid

from tests.conftest import auth


async def test_full_order_flow(client, venue, menu):
    h = venue.h
    waiter, waiter_id, _ = await venue.login_staff("waiter")
    cook, _, _ = await venue.login_staff("cook", kind="kitchen")
    cashier, _, _ = await venue.login_staff("cashier", kind="pos")

    assert (await client.post("/shifts/open", headers=auth(cashier), json={"opening_cash": 100000})).status_code == 201

    table = menu["tables"][0]
    r = await client.post("/orders", headers=auth(waiter), json={
        "table_id": table["id"], "guests": 2,
        "items": [
            {"menu_item_id": menu["plov"]["id"], "quantity": 2, "modifier_ids": [sauce(menu)]},
            {"menu_item_id": menu["tea"]["id"], "quantity": 1, "comment": "без сахара"},
        ]})
    assert r.status_code == 201, r.text
    order = r.json()
    assert order["number"] == 1 and order["status"] == "open" and order["waiter_id"] == waiter_id
    # Плов 350 + соус 50 = 400 сом * 2 + чай 80 = 880 сом
    assert order["subtotal"] == 88000 and order["total"] == 88000
    plov_item = next(i for i in order["items"] if i["name"] == "Плов")
    assert plov_item["station_id"] == menu["hot"]["id"] and plov_item["modifiers"][0]["name"] == "Двойной соус"

    # Стол занят — второй заказ не создать (частичный уникальный индекс)
    r = await client.post("/orders", headers=auth(waiter), json={"table_id": table["id"]})
    assert r.status_code == 409 and r.json()["code"] == "table_busy"

    # Кухня видит свои позиции; бегунок ушёл в очередь печати на принтер станции
    kitchen = (await client.get("/kitchen/items", headers=auth(cook),
                                params={"station_ids": [menu["hot"]["id"]]})).json()
    assert [k["name"] for k in kitchen] == ["Плов"] and kitchen[0]["table_name"] == "1"
    jobs = (await client.get("/print-jobs", headers=auth(cashier))).json()
    assert [j["kind"] for j in jobs] == ["kitchen_ticket"] and jobs[0]["printer_id"] == menu["printer"]["id"]

    # Переходы статусов проверяет сервер
    r = await client.patch(f"/order-items/{plov_item['id']}/status", headers=auth(waiter), json={"status": "cooking"})
    assert r.status_code == 403
    r = await client.patch(f"/order-items/{plov_item['id']}/status", headers=auth(cook), json={"status": "served"})
    assert r.status_code == 409  # нельзя отдать, не приготовив
    r = await client.patch(f"/order-items/{plov_item['id']}/status", headers=auth(cook), json={"status": "ready"})
    assert r.status_code == 200
    r = await client.patch(f"/order-items/{plov_item['id']}/status", headers=auth(waiter), json={"status": "served"})
    assert r.status_code == 200
    r = await client.patch(f"/order-items/{plov_item['id']}/status", headers=auth(cook), json={"status": "cooking"})
    assert r.status_code == 409 and r.json()["code"] == "invalid_transition"

    tea_item = next(i for i in order["items"] if i["name"] == "Чай")
    for st in ("cooking", "ready"):
        r = await client.patch(f"/order-items/{tea_item['id']}/status", headers=auth(cook), json={"status": st})
        assert r.status_code == 200, r.text

    # Официант получает личное событие «готово»
    events = (await client.get("/events", headers=auth(waiter))).json()["events"]
    types = [e["type"] for e in events]
    assert "ORDER_CREATED" in types and "ITEMS_ADDED" in types
    assert any(e["type"] == "ITEM_STATUS" and e["data"]["status"] == "ready" for e in events)

    # Скидка 10% (у официанта нет права)
    r = await client.post(f"/orders/{order['id']}/discount", headers=auth(waiter), json={"kind": "percent", "value": 1000})
    assert r.status_code == 403
    r = await client.post(f"/orders/{order['id']}/discount", headers=auth(cashier),
                          json={"kind": "percent", "value": 1000, "reason": "постоянный гость"})
    assert r.json()["total"] == 79200

    r = await client.post(f"/orders/{order['id']}/bill", headers=auth(waiter))
    assert r.json()["status"] == "billed"

    # Разделённая оплата: часть наличными (со сдачей), часть картой
    r = await client.post(f"/orders/{order['id']}/payments", headers=auth(cashier),
                          json={"method": "cash", "amount": 50000, "tendered": 100000})
    assert r.status_code == 201 and r.json()["status"] == "billed" and r.json()["remaining"] == 29200
    assert r.json()["payments"][0]["change"] == 50000
    r = await client.post(f"/orders/{order['id']}/payments", headers=auth(cashier), json={"method": "card", "amount": 99999})
    assert r.status_code == 409 and r.json()["code"] == "overpayment"
    r = await client.post(f"/orders/{order['id']}/payments", headers=auth(cashier), json={"method": "card", "amount": 29200})
    paid = r.json()
    assert paid["status"] == "paid" and paid["closed_at"] and paid["remaining"] == 0

    # Стол освободился
    tables = (await client.get("/tables", headers=auth(waiter))).json()
    assert next(t for t in tables if t["id"] == table["id"])["open_order_id"] is None

    # Изменение цены в меню не меняет старый заказ
    await client.patch(f"/items/{menu['plov']['id']}", headers=h, json={"price": 99900})
    again = (await client.get(f"/orders/{order['id']}", headers=h)).json()
    assert next(i for i in again["items"] if i["name"] == "Плов")["price"] == 35000
    audit = (await client.get("/audit", params={"action": "menu_item.price_changed"}, headers=h)).json()["items"]
    assert audit[0]["data"] == {"before": 35000, "after": 99900}

    kinds = sorted(j["kind"] for j in (await client.get("/print-jobs", params={"status": "all"}, headers=h)).json())
    assert kinds == ["kitchen_ticket", "precheck", "receipt"]

    # Z-отчёт
    r = await client.post("/shifts/close", headers=auth(cashier), json={"counted_cash": 150000})
    z = r.json()["z_report"]
    assert z["revenue"] == 79200 and z["by_method"] == {"cash": 50000, "card": 29200}
    assert z["expected_cash"] == 150000 and z["cash_difference"] == 0 and z["orders_paid"] == 1


async def test_payment_requires_shift_and_cancel_rules(client, venue, menu):
    waiter, _, _ = await venue.login_staff("waiter")
    cashier, _, _ = await venue.login_staff("cashier", kind="pos")
    cook, _, _ = await venue.login_staff("cook", kind="kitchen")
    order = (await client.post("/orders", headers=auth(waiter), json={
        "table_id": menu["tables"][1]["id"], "items": [{"menu_item_id": menu["lagman"]["id"]}]})).json()
    r = await client.post(f"/orders/{order['id']}/payments", headers=auth(cashier), json={"method": "card", "amount": 30000})
    assert r.status_code == 409 and r.json()["code"] == "shift_not_open"

    item = order["items"][0]
    await client.patch(f"/order-items/{item['id']}/status", headers=auth(cook), json={"status": "cooking"})
    # Блюдо уже готовится — официант не может отменить, нужен orders.cancel и причина
    r = await client.post(f"/orders/{order['id']}/cancel", headers=auth(waiter), json={"reason": "гость ушёл"})
    assert r.status_code == 403
    r = await client.patch(f"/order-items/{item['id']}/status", headers=h_(venue), json={"status": "cancelled"})
    assert r.status_code == 422
    r = await client.patch(f"/order-items/{item['id']}/status", headers=h_(venue),
                           json={"status": "cancelled", "reason": "пересол"})
    assert r.status_code == 200
    assert (await client.get(f"/orders/{order['id']}", headers=venue.h)).json()["total"] == 0
    r = await client.post(f"/orders/{order['id']}/cancel", headers=venue.h, json={"reason": "пусто"})
    assert r.json()["status"] == "cancelled"
    log = (await client.get("/audit", params={"action": "order*"}, headers=venue.h)).json()["items"]
    assert {"order.cancelled", "order_item.cancelled"} <= {a["action"] for a in log}


def sauce(menu):
    return next(m["id"] for m in menu["group"]["modifiers"] if m["name"] == "Двойной соус")


def h_(venue):
    return venue.h


async def test_stop_list_blocks_ordering(client, venue, menu):
    waiter, _, _ = await venue.login_staff("waiter")
    await client.post(f"/items/{menu['lagman']['id']}/stop", headers=venue.h, json={"is_available": False})
    r = await client.post("/orders", headers=auth(waiter), json={"items": [{"menu_item_id": menu["lagman"]["id"]}]})
    assert r.status_code == 409 and r.json()["code"] == "item_unavailable"
    # Модификаторы: не больше max_select
    mods = [m["id"] for m in menu["group"]["modifiers"]]
    r = await client.post("/orders", headers=auth(waiter),
                          json={"items": [{"menu_item_id": menu["plov"]["id"], "modifier_ids": mods}]})
    assert r.status_code == 422 and r.json()["code"] == "invalid_modifiers"


async def test_transfer_and_numbering(client, venue, menu):
    waiter, _, _ = await venue.login_staff("waiter")
    t1, t2, t3 = menu["tables"]
    o1 = (await client.post("/orders", headers=auth(waiter), json={"table_id": t1["id"]})).json()
    o2 = (await client.post("/orders", headers=auth(waiter), json={"table_id": t2["id"]})).json()
    assert (o1["number"], o2["number"]) == (1, 2)
    r = await client.post(f"/orders/{o1['id']}/transfer", headers=venue.h, json={"table_id": t2["id"]})
    assert r.status_code == 409 and r.json()["code"] == "table_busy"
    r = await client.post(f"/orders/{o1['id']}/transfer", headers=venue.h, json={"table_id": t3["id"]})
    assert r.status_code == 200 and r.json()["table_id"] == t3["id"]


async def test_idempotency_key(client, venue, menu):
    waiter, _, _ = await venue.login_staff("waiter")
    key = str(uuid.uuid4())
    body = {"table_id": menu["tables"][0]["id"], "items": [{"menu_item_id": menu["tea"]["id"]}]}
    r1 = await client.post("/orders", headers=auth(waiter, **{"Idempotency-Key": key}), json=body)
    r2 = await client.post("/orders", headers=auth(waiter, **{"Idempotency-Key": key}), json=body)
    assert r1.status_code == r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"] and r2.headers.get("idempotent-replay") == "true"
    page = (await client.get("/orders", headers=auth(waiter))).json()
    assert len(page["items"]) == 1

    r3 = await client.post("/orders", headers=auth(waiter, **{"Idempotency-Key": key}), json={"guests": 3})
    assert r3.status_code == 422 and r3.json()["code"] == "idempotency_conflict"

    # Ошибка не «сжигает» ключ: после исправления повтор с тем же ключом проходит
    key2 = str(uuid.uuid4())
    bad = {"table_id": menu["tables"][0]["id"]}  # стол занят
    assert (await client.post("/orders", headers=auth(waiter, **{"Idempotency-Key": key2}), json=bad)).status_code == 409
    ok = {"table_id": menu["tables"][1]["id"]}
    assert (await client.post("/orders", headers=auth(waiter, **{"Idempotency-Key": key2}), json=ok)).status_code == 201


async def test_client_generated_ids(client, venue, menu):
    """Офлайн-очередь: клиент сам создаёт UUID заказа и позиций."""
    waiter, _, _ = await venue.login_staff("waiter")
    oid, iid = str(uuid.uuid4()), str(uuid.uuid4())
    r = await client.post("/orders", headers=auth(waiter), json={
        "id": oid, "items": [{"id": iid, "menu_item_id": menu["tea"]["id"]}]})
    assert r.json()["id"] == oid and r.json()["items"][0]["id"] == iid
    r = await client.post("/orders", headers=auth(waiter), json={"id": oid})
    assert r.status_code == 409


async def test_cursor_pagination(client, venue, menu):
    for _ in range(5):
        await client.post("/orders", headers=venue.h, json={})
    page1 = (await client.get("/orders", params={"limit": 2}, headers=venue.h)).json()
    page2 = (await client.get("/orders", params={"limit": 2, "cursor": page1["next_cursor"]}, headers=venue.h)).json()
    page3 = (await client.get("/orders", params={"limit": 2, "cursor": page2["next_cursor"]}, headers=venue.h)).json()
    nums = [o["number"] for p in (page1, page2, page3) for o in p["items"]]
    assert nums == [5, 4, 3, 2, 1] and page3["next_cursor"] is None
