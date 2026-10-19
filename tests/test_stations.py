from tests.conftest import auth


async def test_split_merge_delete(client, venue, menu):
    h = venue.h
    waiter, _, _ = await venue.login_staff("waiter")
    # Открытая позиция на «Горячем»
    order = (await client.post("/orders", headers=auth(waiter), json={
        "items": [{"menu_item_id": menu["lagman"]["id"]}, {"menu_item_id": menu["plov"]["id"]}]})).json()
    lagman_oi = next(i for i in order["items"] if i["name"] == "Лагман")

    # Разделить: из «Горячее» создаётся «Супы», переезжает лагман вместе с незакрытой позицией
    r = await client.post(f"/stations/{menu['hot']['id']}/split", headers=h, json={
        "new_station": {"name": "Супы"}, "item_ids": [menu["lagman"]["id"]]})
    assert r.status_code == 200, r.text
    soups = r.json()["station"]
    assert r.json()["moved_items"] == 1 and r.json()["moved_open_order_items"] == 1
    item = (await client.get(f"/items/{menu['lagman']['id']}", headers=h)).json()
    assert item["station_id"] == soups["id"]
    o = (await client.get(f"/orders/{order['id']}", headers=h)).json()
    assert next(i for i in o["items"] if i["id"] == lagman_oi["id"])["station_id"] == soups["id"]

    # Удалить без указания получателя нельзя
    r = await client.delete(f"/stations/{soups['id']}", headers=h)
    assert r.status_code == 409 and r.json()["code"] == "station_in_use"

    # Объединить: «Супы» → «Горячее», «Супы» архивируется
    r = await client.post(f"/stations/{soups['id']}/merge", headers=h, json={"target_id": menu["hot"]["id"]})
    assert r.status_code == 200 and r.json()["moved_items"] == 1
    stations = (await client.get("/stations", headers=h)).json()
    assert soups["id"] not in [s["id"] for s in stations]
    archived = (await client.get("/stations", params={"include_archived": True}, headers=h)).json()
    assert next(s for s in archived if s["id"] == soups["id"])["archived_at"]

    # Удалить «Бар» с передачей блюд на «Горячее»
    r = await client.delete(f"/stations/{menu['bar']['id']}", params={"move_to": menu["hot"]["id"]}, headers=h)
    assert r.status_code == 200 and r.json()["moved_items"] == 1
    cat = next(c for c in (await client.get("/categories", headers=h)).json() if c["id"] == menu["drinks"]["id"])
    assert cat["default_station_id"] == menu["hot"]["id"]

    events = (await client.get("/events", headers=h)).json()["events"]
    ops = [e["data"]["op"] for e in events if e["type"] == "STATIONS_CHANGED"]
    assert ops[-3:] == ["split", "merged", "deleted"]
    log = (await client.get("/audit", params={"action": "station.*"}, headers=h)).json()["items"]
    assert {"station.split", "station.merged", "station.deleted"} <= {a["action"] for a in log}


async def test_disable_station_puts_items_in_stop_list(client, venue, menu):
    waiter, _, _ = await venue.login_staff("waiter")
    r = await client.patch(f"/stations/{menu['bar']['id']}", headers=venue.h, json={"is_active": False})
    assert r.status_code == 200
    tea = (await client.get(f"/items/{menu['tea']['id']}", headers=venue.h)).json()
    assert tea["is_available"] is True and tea["available_now"] is False
    r = await client.post("/orders", headers=auth(waiter), json={"items": [{"menu_item_id": menu["tea"]["id"]}]})
    assert r.json()["code"] == "item_unavailable"
    public = (await client.get(f"/public/{venue.slug}/menu")).json()
    tea_pub = next(i for c in public["categories"] for i in c["items"] if i["id"] == menu["tea"]["id"])
    assert tea_pub["available"] is False


async def test_cook_bound_to_station(client, venue, menu):
    cook, _, _ = await venue.login_staff("cook", kind="kitchen", station_ids=[menu["bar"]["id"]])
    waiter, _, _ = await venue.login_staff("waiter")
    order = (await client.post("/orders", headers=auth(waiter), json={
        "items": [{"menu_item_id": menu["tea"]["id"]}, {"menu_item_id": menu["plov"]["id"]}]})).json()
    visible = (await client.get("/kitchen/items", headers=auth(cook))).json()
    assert [k["name"] for k in visible] == ["Чай"]
    plov = next(i for i in order["items"] if i["name"] == "Плов")
    r = await client.patch(f"/order-items/{plov['id']}/status", headers=auth(cook), json={"status": "cooking"})
    assert r.status_code == 403 and r.json()["code"] == "station_forbidden"

    # Событие о новой позиции пришло только в канал станции бара
    events = (await client.get("/events", headers=auth(cook))).json()["events"]
    added = [e for e in events if e["type"] == "ITEMS_ADDED"]
    assert added and all(i["station_id"] in (menu["bar"]["id"], menu["hot"]["id"]) for i in added[0]["data"]["items"])
