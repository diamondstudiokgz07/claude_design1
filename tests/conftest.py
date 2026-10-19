"""Тесты на настоящем PostgreSQL 16 (+ Redis).

По умолчанию — локальные серверы; адреса переопределяются переменными окружения:
  TEST_ADMIN_DATABASE_URL — владелец схемы (миграции), TEST_DATABASE_URL — роль приложения без BYPASSRLS.
"""

import os
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADMIN_URL = os.environ.get("TEST_ADMIN_DATABASE_URL", "postgresql+asyncpg://dostuk:dostuk@localhost:5432/dostuk_test")
APP_URL = os.environ.get("TEST_DATABASE_URL", "postgresql+asyncpg://dostuk_app:dostuk_app@localhost:5432/dostuk_test")
os.environ["DOSTUK_DATABASE_URL"] = APP_URL
os.environ["DOSTUK_MIGRATIONS_DATABASE_URL"] = ADMIN_URL
os.environ["DOSTUK_REDIS_URL"] = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
os.environ["DOSTUK_STORAGE_LOCAL_DIR"] = str(ROOT / ".test-files")
os.environ["DOSTUK_JWT_SECRET"] = "test-secret-test-secret-test-secret"

import asyncpg  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402

from app.core.redis import close_redis, get_redis  # noqa: E402
from app.db import dispose_engine, tenant_session  # noqa: E402


def _pg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture(scope="session", autouse=True)
async def database():
    conn = await asyncpg.connect(_pg_dsn(ADMIN_URL))
    await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    await conn.close()
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, check=True, env=os.environ.copy())
    yield
    await dispose_engine()
    await close_redis()


@pytest.fixture(autouse=True)
async def _clean_redis():
    await get_redis().flushdb()
    yield


@pytest.fixture
async def client():
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test/api/v1") as c:
        yield c


def auth(token: str, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **extra}


class Venue:
    """Заведение с владельцем, ролями и помощниками для создания сотрудников и устройств."""

    def __init__(self, client: httpx.AsyncClient, venue_id: uuid.UUID, slug: str, owner_email: str, password: str):
        self.c = client
        self.id = venue_id
        self.slug = slug
        self.owner_email = owner_email
        self.password = password
        self.owner_token = ""
        self.roles: dict[str, str] = {}

    async def login_owner(self) -> str:
        r = await self.c.post("/auth/login", json={"email": self.owner_email, "password": self.password})
        assert r.status_code == 200, r.text
        self.owner_token = r.json()["access_token"]
        roles = (await self.c.get("/roles", headers=auth(self.owner_token))).json()
        self.roles = {x["name"]: x["id"] for x in roles}
        return self.owner_token

    @property
    def h(self) -> dict[str, str]:
        return auth(self.owner_token)

    async def pair_device(self, kind: str = "waiter", name: str = "Телефон") -> str:
        r = await self.c.post("/auth/device/pairing-codes", json={"name": name, "kind": kind}, headers=self.h)
        assert r.status_code == 201, r.text
        r = await self.c.post("/auth/device/pair", json={"code": r.json()["code"], "platform": "android"})
        assert r.status_code == 200, r.text
        return r.json()["device_token"]

    async def staff(self, role: str, pin: str = "1234", name: str | None = None, station_ids=None) -> str:
        r = await self.c.post("/staff", headers=self.h, json={
            "name": name or f"{role}-{uuid.uuid4().hex[:4]}", "role_id": self.roles[role], "pin": pin,
            "station_ids": [str(s) for s in (station_ids or [])]})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    async def login_staff(self, role: str, kind: str = "waiter", station_ids=None) -> tuple[str, str, str]:
        """Возвращает (access_token, staff_id, device_token)."""
        staff_id = await self.staff(role, station_ids=station_ids)
        device = await self.pair_device(kind)
        r = await self.c.post("/auth/pin", json={"staff_id": staff_id, "pin": "1234"},
                              headers={"X-Device-Token": device})
        assert r.status_code == 200, r.text
        return r.json()["access_token"], staff_id, device


async def make_venue(client: httpx.AsyncClient, org_id: uuid.UUID | None = None) -> Venue:
    from app.modules.venues.schemas import BootstrapIn, VenueCreate
    from app.modules.venues.service import bootstrap, create_venue

    slug = f"cafe-{uuid.uuid4().hex[:8]}"
    email = f"owner-{uuid.uuid4().hex[:6]}@example.kg"
    password = "very-secret-pass"
    async with tenant_session(None, bypass=True) as s:
        if org_id is None:
            _, venue, _ = await bootstrap(s, BootstrapIn(
                organization="Сеть", venue=VenueCreate(name="Кафе", slug=slug), owner_name="Владелец",
                owner_email=email, owner_password=password))
        else:
            venue = await create_venue(s, org_id, name="Филиал", slug=slug)
    v = Venue(client, venue.id, slug, email, password)
    if org_id is None:
        await v.login_owner()
    return v


@pytest.fixture
async def venue(client) -> Venue:
    return await make_venue(client)


@pytest.fixture
async def menu(client, venue):
    """Станции Горячее/Бар, категория, блюда, группа модификаторов, принтер кухни."""
    h = venue.h
    printer = (await client.post("/printers", headers=h, json={"name": "Кухня", "kind": "kitchen",
                                                               "address": "192.168.1.50:9100"})).json()
    hot = (await client.post("/stations", headers=h, json={"name": "Горячее", "printer_id": printer["id"]})).json()
    bar = (await client.post("/stations", headers=h, json={"name": "Бар", "color": "#3366FF"})).json()
    cat = (await client.post("/categories", headers=h, json={"name": "Основное", "default_station_id": hot["id"],
                                                             "translations": {"ky": "Негизги"}})).json()
    drinks = (await client.post("/categories", headers=h, json={"name": "Напитки", "default_station_id": bar["id"]})).json()
    group = (await client.post("/modifiers", headers=h, json={
        "name": "Острота", "min_select": 0, "max_select": 1,
        "modifiers": [{"name": "Острый", "price": 0}, {"name": "Двойной соус", "price": 5000}]})).json()
    plov = (await client.post("/items", headers=h, json={
        "category_id": cat["id"], "name": "Плов", "price": 35000, "weight_grams": 350,
        "translations": {"ky": {"name": "Палоо"}}, "modifier_group_ids": [group["id"]]})).json()
    lagman = (await client.post("/items", headers=h, json={"category_id": cat["id"], "name": "Лагман", "price": 30000})).json()
    tea = (await client.post("/items", headers=h, json={"category_id": drinks["id"], "name": "Чай", "price": 8000})).json()
    hall = (await client.post("/halls", headers=h, json={"name": "Основной зал"})).json()
    tables = [(await client.post("/tables", headers=h, json={"hall_id": hall["id"], "name": str(n)})).json()
              for n in (1, 2, 3)]
    for x in (printer, hot, bar, cat, group, plov, lagman, tea, hall, *tables):
        assert "id" in x, x
    return {"printer": printer, "hot": hot, "bar": bar, "cat": cat, "drinks": drinks, "group": group,
            "plov": plov, "lagman": lagman, "tea": tea, "hall": hall, "tables": tables}
