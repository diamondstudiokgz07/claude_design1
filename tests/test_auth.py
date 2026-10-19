from tests.conftest import auth


async def test_device_pairing_and_pin_login(client, venue):
    staff_id = await venue.staff("waiter", pin="4321", name="Айбек")
    r = await client.post("/auth/device/pairing-codes", json={"name": "Телефон 1", "kind": "waiter"}, headers=venue.h)
    assert r.status_code == 201
    code = r.json()["code"]

    r = await client.post("/auth/device/pair", json={"code": code, "platform": "android", "app_version": "3.0.0"})
    assert r.status_code == 200, r.text
    device_token = r.json()["device_token"]
    assert r.json()["venue"]["slug"] == venue.slug

    # Код одноразовый
    r = await client.post("/auth/device/pair", json={"code": code})
    assert r.status_code == 400 and r.json()["code"] == "invalid_pairing_code"

    # PIN без устройства ничего не открывает
    r = await client.post("/auth/pin", json={"staff_id": staff_id, "pin": "4321"})
    assert r.status_code == 401 and r.json()["code"] == "device_required"

    r = await client.get("/auth/device/staff", headers={"X-Device-Token": device_token})
    assert any(s["id"] == staff_id for s in r.json())

    r = await client.post("/auth/pin", json={"staff_id": staff_id, "pin": "4321"}, headers={"X-Device-Token": device_token})
    assert r.status_code == 200, r.text
    tokens = r.json()

    me = (await client.get("/auth/me", headers=auth(tokens["access_token"]))).json()
    assert me["staff"]["name"] == "Айбек"
    assert "orders.create" in me["permissions"] and "menu.edit" not in me["permissions"]
    assert me["device"]["kind"] == "waiter"

    # Ротация refresh-токена; повтор старого токена отзывает все сессии устройства
    r = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200
    new = r.json()
    r = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401
    r = await client.get("/auth/me", headers=auth(new["access_token"]))
    assert r.status_code == 401


async def test_error_language(client, venue):
    r = await client.post("/auth/pin", json={"staff_id": venue.roles["admin"], "pin": "0000"},
                          headers={"Accept-Language": "ky"})
    assert r.json()["message"] == "Байланган түзмөк керек"
    r = await client.post("/auth/pin", json={"staff_id": venue.roles["admin"], "pin": "0000"})
    assert r.json() == {"code": "device_required", "message": "Нужно привязанное устройство", "details": {}}


async def test_pin_lockout_notifies_admin(client, venue):
    staff_id = await venue.staff("waiter", pin="1111")
    device = await venue.pair_device()
    codes = []
    for _ in range(5):
        r = await client.post("/auth/pin", json={"staff_id": staff_id, "pin": "9999"}, headers={"X-Device-Token": device})
        codes.append(r.json()["code"])
    assert codes[:4] == ["invalid_pin"] * 4 and codes[4] == "pin_locked"
    # Даже верный PIN не пускает во время блокировки
    r = await client.post("/auth/pin", json={"staff_id": staff_id, "pin": "1111"}, headers={"X-Device-Token": device})
    assert r.status_code == 429

    audit = (await client.get("/audit", params={"action": "auth.pin_*"}, headers=venue.h)).json()["items"]
    assert {a["action"] for a in audit} == {"auth.pin_failed", "auth.pin_locked"}
    events = (await client.get("/events", headers=venue.h)).json()["events"]
    assert any(e["type"] == "SECURITY_ALERT" for e in events)

    # Админ сбрасывает PIN — блокировка снимается
    await client.put(f"/staff/{staff_id}/pin", json={"pin": "2222"}, headers=venue.h)
    r = await client.post("/auth/pin", json={"staff_id": staff_id, "pin": "2222"}, headers={"X-Device-Token": device})
    assert r.status_code == 200


async def test_device_revocation(client, venue):
    token, staff_id, device = await venue.login_staff("waiter")
    devices = (await client.get("/devices", headers=venue.h)).json()
    dev_id = next(d["id"] for d in devices if d["revoked_at"] is None)
    r = await client.post(f"/devices/{dev_id}/revoke", headers=venue.h)
    assert r.status_code == 200
    assert (await client.get("/auth/me", headers=auth(token))).status_code == 401
    r = await client.post("/auth/pin", json={"staff_id": staff_id, "pin": "1234"}, headers={"X-Device-Token": device})
    assert r.status_code == 401


async def test_rbac_server_side(client, venue, menu):
    waiter, _, _ = await venue.login_staff("waiter")
    r = await client.post("/items", headers=auth(waiter), json={"category_id": menu["cat"]["id"], "name": "X", "price": 1})
    assert r.status_code == 403 and r.json()["details"]["permission"] == "menu.edit"

    # Админ меняет права роли сам — действует сразу
    perms = (await client.get("/roles", headers=venue.h)).json()
    waiter_perms = next(r["permissions"] for r in perms if r["name"] == "waiter")
    r = await client.put(f"/roles/{venue.roles['waiter']}/permissions", headers=venue.h,
                         json={"permissions": waiter_perms + ["menu.stop"]})
    assert r.status_code == 200
    r = await client.post(f"/items/{menu['plov']['id']}/stop", headers=auth(waiter), json={"is_available": False})
    assert r.status_code == 200 and r.json()["is_available"] is False


async def test_owner_totp_and_logout(client, venue):
    import pyotp

    r = await client.post("/auth/totp/setup", headers=venue.h)
    secret = r.json()["secret"]
    r = await client.post("/auth/totp/confirm", headers=venue.h, json={"code": pyotp.TOTP(secret).now()})
    assert r.status_code == 204
    r = await client.post("/auth/login", json={"email": venue.owner_email, "password": venue.password})
    assert r.json()["code"] == "totp_required"
    r = await client.post("/auth/login", json={"email": venue.owner_email, "password": venue.password,
                                               "totp_code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200
    token = r.json()["access_token"]
    assert (await client.post("/auth/logout", headers=auth(token))).status_code == 204
    assert (await client.get("/auth/me", headers=auth(token))).status_code == 401
