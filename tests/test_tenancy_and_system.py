import asyncpg
import pytest

from tests.conftest import APP_URL, _pg_dsn, auth, make_venue


async def test_rls_isolation(client, venue, menu):
    other = await make_venue(client)
    # Через API: стол чужого заведения не найти даже по QR-токену
    qr = menu["tables"][0]["qr_token"]
    r = await client.get(f"/tables/by-qr/{qr}", headers=other.h)
    assert r.status_code == 404
    assert (await client.get(f"/items/{menu['plov']['id']}", headers=other.h)).status_code == 404
    assert (await client.get("/tables", headers=other.h)).json() == []

    # На уровне БД: роль приложения без контекста заведения не видит ничего
    conn = await asyncpg.connect(_pg_dsn(APP_URL))
    try:
        assert await conn.fetchval("SELECT count(*) FROM menu_items") == 0
        await conn.execute("SELECT set_config('app.venue_id', $1, false)", str(venue.id))
        assert await conn.fetchval("SELECT count(*) FROM menu_items") == 3
        assert await conn.fetchval("SELECT count(*) FROM venues") == 1
        # Вставить строку в чужое заведение нельзя
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute("INSERT INTO halls (id, venue_id, name, sort_order, is_active) "
                               "VALUES (gen_random_uuid(), $1, 'x', 0, true)", other.id)
    finally:
        await conn.close()


async def test_audit_log_is_append_only(client, venue):
    conn = await asyncpg.connect(_pg_dsn(APP_URL))
    try:
        await conn.execute("SELECT set_config('app.venue_id', $1, false)", str(venue.id))
        assert await conn.fetchval("SELECT count(*) FROM audit_log") > 0
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute("DELETE FROM audit_log")
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute("UPDATE audit_log SET action = 'x'")
    finally:
        await conn.close()


async def test_owner_switches_between_venues(client, venue):
    from app.db import tenant_session
    from app.modules.venues.models import Venue as VenueModel

    async with tenant_session(None, bypass=True) as s:
        org_id = (await s.get(VenueModel, venue.id)).organization_id
    branch = await make_venue(client, org_id=org_id)
    venues = (await client.get("/venues", headers=venue.h)).json()
    assert {v["slug"] for v in venues} == {venue.slug, branch.slug}
    r = await client.post("/auth/switch-venue", headers=venue.h, json={"venue_id": str(branch.id)})
    assert r.status_code == 200
    token = r.json()["access_token"]
    assert (await client.get("/venue", headers=auth(token))).json()["slug"] == branch.slug
    # Чужая сеть недоступна
    foreign = await make_venue(client)
    r = await client.post("/auth/switch-venue", headers=venue.h, json={"venue_id": str(foreign.id)})
    assert r.status_code == 403


async def test_events_sync_and_outbox_publisher(client, venue, menu):
    from app.modules.realtime.hub import publish_pending

    r = await client.get("/events", params={"since": 0, "limit": 3}, headers=venue.h)
    page = r.json()
    assert len(page["events"]) == 3 and page["has_more"] is True
    seqs = [e["seq"] for e in page["events"]]
    assert seqs == sorted(seqs)
    r2 = (await client.get("/events", params={"since": page["last_seq"]}, headers=venue.h)).json()
    assert all(e["seq"] > page["last_seq"] for e in r2["events"])

    assert await publish_pending() > 0
    assert await publish_pending() == 0  # опубликованные не отправляются повторно


async def test_public_menu_and_cache_invalidation(client, venue, menu):
    r = await client.get(f"/public/{venue.slug}/menu", params={"lang": "ky"})
    assert r.status_code == 200
    data = r.json()
    names = {i["name"] for c in data["categories"] for i in c["items"]}
    assert "Палоо" in names  # перевод
    assert next(c for c in data["categories"] if c["id"] == menu["cat"]["id"])["name"] == "Негизги"
    await client.post(f"/items/{menu['plov']['id']}/stop", headers=venue.h, json={"is_available": False})
    data = (await client.get(f"/public/{venue.slug}/menu", params={"lang": "ky"})).json()
    plov = next(i for c in data["categories"] for i in c["items"] if i["id"] == menu["plov"]["id"])
    assert plov["available"] is False

    qr = menu["tables"][0]["qr_token"]
    r = await client.post(f"/public/{venue.slug}/call-waiter", json={"t": qr})
    assert r.status_code == 202
    assert (await client.get("/public/no-such-cafe/menu")).status_code == 404


async def test_reports_and_excel(client, venue, menu):
    from datetime import date, timedelta

    await client.post("/shifts/open", headers=venue.h, json={})
    o = (await client.post("/orders", headers=venue.h, json={"items": [{"menu_item_id": menu["tea"]["id"], "quantity": 3}]})).json()
    await client.post(f"/orders/{o['id']}/payments", headers=venue.h, json={"method": "qr", "amount": 24000})
    today = date.today()
    params = {"date_from": str(today - timedelta(days=1)), "date_to": str(today + timedelta(days=1))}
    rep = (await client.get("/reports/sales", params=params, headers=venue.h)).json()
    assert rep["revenue"] == 24000 and rep["by_item"][0] == {"menu_item_id": menu["tea"]["id"], "name": "Чай",
                                                             "quantity": 3, "revenue": 24000}
    x = await client.get("/reports/sales.xlsx", params=params, headers=venue.h)
    assert x.status_code == 200 and x.content[:2] == b"PK"

    r = await client.post("/shifts/expenses", headers=venue.h, json={"kind": "expense", "amount": 5000, "comment": "хлеб"})
    assert r.status_code == 201
    xr = (await client.get("/shifts/current/x-report", headers=venue.h)).json()
    assert xr["cash_movements"] == {"expense": 5000} and xr["by_method"] == {"qr": 24000}


async def test_photo_upload(client, venue, menu):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (2000, 1200), (200, 120, 40)).save(buf, "JPEG")
    r = await client.post(f"/items/{menu['plov']['id']}/photo", headers=venue.h,
                          files={"file": ("plov.jpg", buf.getvalue(), "image/jpeg")})
    assert r.status_code == 200, r.text
    assert r.json()["photo_url"].endswith(".webp") and r.json()["photo_thumb_url"].endswith("_400.webp")
    r = await client.post(f"/items/{menu['plov']['id']}/photo", headers=venue.h,
                          files={"file": ("x.txt", b"hello", "text/plain")})
    assert r.status_code == 422
