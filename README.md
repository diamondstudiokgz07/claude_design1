# Достук 3.0 — backend

Облачный backend по документу «Достук 3.0 — архитектура системы»: модульный монолит на
**Python 3.12 + FastAPI + SQLAlchemy 2 (async) + PostgreSQL 16 + Redis 7 + arq + MinIO**.
Одна база на все заведения, изоляция — фильтр по `venue_id` в коде **и** Row Level Security в PostgreSQL.

## Структура

```
app/
  main.py            сборка приложения, /api/v1, /health, OpenAPI → /api/v1/openapi.json
  config.py          настройки (переменные окружения DOSTUK_*)
  db.py              движок, Base, контекст RLS (set_config app.venue_id)
  core/              ошибки {code,message,details} ky/ru, JWT/Argon2, права, Redis-лимиты,
                     курсорная пагинация, Idempotency-Key, хранилище файлов
  modules/<имя>/     models.py · schemas.py · service.py · router.py
    identity         сотрудники, PIN, устройства, токены, роли и права
    venues           организации, заведения, залы, столы, QR, настройки
    menu             категории, блюда, переводы, модификаторы, стоп-лист, фото
    stations         кухонные станции: создать / разделить / объединить / удалить / выключить
    orders           заказы, позиции, статусы, нумерация, экран кухни
    billing          пречек, оплаты (разделённые), скидки, возвраты, фискализация
    shifts           смены, расходы/внесения/инкассации, X/Z-отчёты
    reports          аналитика продаж, выгрузка в Excel
    realtime         outbox, WebSocket-шлюз, добор событий
    printing         принтеры и очередь заданий печати для «Кассы»
    audit            неизменяемый журнал
    public           API сайта-меню (без авторизации, кэш, лимиты)
web/index.html       тест-консоль (HTML-прототип) — отдаётся на /app/
worker/main.py       arq: публикатор outbox, фискализация, автозакрытие смен, очистка
alembic/versions/    0001 — схема, 0002 — RLS, права роли приложения, триггер audit_log
deploy/              Caddyfile, init-db.sh
tests/               pytest на настоящем PostgreSQL + Redis (23 теста)
```

## База данных

* Ключи — UUID (клиент может создать офлайн), деньги — `bigint` в тыйынах, время — `timestamptz`.
* 33 таблицы: все из раздела 4 + `order_counters` (атомарная нумерация заказов на бизнес-день)
  и `menu_item_modifier_groups` (связь блюд с группами модификаторов).
* `orders`: уникальный номер `(venue_id, business_date, number)`; один открытый заказ на стол —
  частичный уникальный индекс `uq_orders_one_open_per_table`.
* Позиция заказа копирует название, цену и станцию — изменение меню не меняет старые чеки.
* `outbox_events.seq` — сквозной номер; advisory-lock на заведение гарантирует, что события
  одного заведения фиксируются строго по порядку seq, и добор `since=seq` ничего не теряет.
* **RLS**: на всех таблицах с `venue_id` политика `venue_id = app_venue()`, `FORCE ROW LEVEL SECURITY`.
  Приложение подключается ролью `dostuk_app` без BYPASSRLS; миграции — владельцем схемы.
  Без установленного заведения роль приложения не видит ни одной строки.
* `audit_log`: у роли приложения отозваны UPDATE/DELETE, плюс триггер, запрещающий изменения.

## Безопасность

1. **Привязка устройства**: админ (`devices.manage`) создаёт одноразовый код на 10 минут →
   `POST /auth/device/pair` → долгоживущий device-токен (в БД хранится только SHA-256).
2. **Вход сотрудника**: `POST /auth/pin` с заголовком `X-Device-Token` → access JWT (15 мин) +
   refresh (до 12 ч, ротация; повтор старого refresh отзывает все сессии устройства).
3. **Перебор PIN**: лимит на сотрудника (5) и на устройство (20) в Redis, блокировка 15 мин,
   событие `SECURITY_ALERT` админу и запись в журнал.
4. **Владелец/админ из дома**: email/телефон + пароль + TOTP (`/auth/totp/setup`, `/auth/totp/confirm`);
   владелец переключается между заведениями сети (`/auth/switch-venue`).
5. **RBAC**: каждый эндпоинт объявляет право (`require("orders.cancel")`), права роли загружаются
   на каждый запрос — изменения админа действуют сразу. Каталог — `GET /permissions`.
6. Заведение берётся только из токена. Отзыв устройства/сотрудника → `SESSION_REVOKED`.

## API (`/api/v1`)

| Группа | Эндпоинты |
|---|---|
| auth | `POST /auth/device/pairing-codes`, `POST /auth/device/pair`, `GET /auth/device/staff`, `POST /auth/pin`, `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`, `GET /auth/me`, `POST /auth/switch-venue`, `POST /auth/totp/{setup,confirm}` |
| venues | `/venue`, `/venues`, `/settings`, `/halls`, `/tables`, `/tables/by-qr/{token}`, `/tables/{id}/regenerate-qr` |
| menu | `/categories`, `/items`, `/items/{id}/stop`, `/stop-list`, `/items/{id}/photo`, `/modifiers` |
| stations | `/stations`, `/stations/{id}/split`, `/stations/{id}/merge`, `DELETE /stations/{id}?move_to=` |
| orders | `POST /orders`, `GET /orders`, `POST /orders/{id}/items`, `PATCH /order-items/{id}/status`, `POST /orders/{id}/transfer`, `POST /orders/{id}/cancel`, `POST /orders/{id}/confirm`, `GET /kitchen/items` |
| billing | `POST /orders/{id}/bill`, `POST /orders/{id}/payments`, `POST /orders/{id}/discount`, `POST /payments/{id}/refund` |
| shifts | `POST /shifts/open`, `GET /shifts/current`, `GET /shifts/current/x-report`, `POST /shifts/close`, `/shifts/expenses`, `GET /shifts` |
| staff | `/staff`, `/staff/{id}/pin`, `/staff/{id}/stations`, `/roles`, `/roles/{id}/permissions`, `/permissions`, `/devices`, `/devices/{id}/revoke`, `/audit` |
| printing | `/printers`, `GET /print-jobs`, `POST /print-jobs/{id}/ack`, `POST /print-jobs/{id}/retry` |
| reports | `GET /reports/sales`, `GET /reports/sales.xlsx` |
| sync | `GET /events?since={seq}`, `WS /ws?token=…&since=…&stations=id,id` |
| public | `GET /public/{slug}/menu?lang=ky\|ru`, `GET /public/{slug}/table/{token}`, `POST /public/{slug}/call-waiter` |

Правила: изменяющие запросы принимают `Idempotency-Key` (UUID) — повтор возвращает сохранённый
ответ с заголовком `Idempotent-Replay: true`; ошибки — `{code, message, details}` на языке из
`Accept-Language`; списки — курсорная пагинация (`cursor`, `next_cursor`).

**События** `{seq, type, at, data}`: ORDER_CREATED, ITEMS_ADDED, ITEM_STATUS, ORDER_UPDATED,
ORDER_PAID, ORDER_CANCELLED, STOP_LIST, MENU_CHANGED, STATIONS_CHANGED, SHIFT, SESSION_REVOKED,
PRINT_JOB, WAITER_CALLED, SECURITY_ALERT. Каналы: `venue:{id}:floor`, `venue:{id}:admin`,
`user:{id}`, `station:{id}`, `device:{id}` — сервер подписывает соединение сам по правам.

**Статусы.** Заказ: `pending` (с сайта) → `open` → `billed` → `paid`, или `cancelled`.
Позиция: `queued` → `cooking` → `ready` → `served`, отмена из любого статуса (после начала
готовки — право `orders.cancel` и причина). Переходы — `ITEM_TRANSITIONS` в `orders/service.py`.

## Тест-консоль (HTML-прототип)

Пока нет Flutter-приложений, всё проверяется через `web/index.html` — backend сам отдаёт её по адресу
**`/app/`** (например `https://api.dostuk.kg/app/` или `http://localhost:8000/app/`). Один файл,
без сборки и внешних библиотек; ходит в тот же API, что и будущие приложения.

* **Вход** как в приложениях: владелец — email + пароль (+2FA); устройство — код привязки → выбор
  сотрудника → PIN.
* У каждой **вкладки браузера своё «устройство»** (sessionStorage): в одном окне можно открыть
  официанта, кухню и кассу одновременно и смотреть, как события приходят по WebSocket.
* Вкладки по правам роли: **Зал** (столы, заказ, добавки, пречек, перенос, отмена), **Кухня**
  (выбор станций, билеты с таймерами, звук на новый заказ, стоп-лист), **Касса** (смена, разделённая
  оплата, скидка, возврат, X/Z-отчёт, очередь печати с текстом чека), **Меню**, **Станции**
  (разделить / объединить / удалить / выключить), **Настройка** (залы, столы, QR, принтеры),
  **Персонал** (сотрудники, матрица прав ролей, коды привязки, отзыв устройств), **Отчёты** (+Excel,
  журнал), **Сайт-меню** (как видит гость, ky/ru), **События** (лог WebSocket), **Инструменты**
  (демо-данные, API-консоль).
* **Демо-данные** (Инструменты): станции, меню на ky/ru, 8 столов, принтеры и сотрудники
  Официант PIN `1111`, Повар `2222`, Кассир `3333`.

Сценарий проверки: владелец → «Инструменты → Заполнить демо-данными» → «Персонал → Создать код
привязки» (официант, кухня, касса) → в трёх новых вкладках ввести коды и войти по PIN → официант
открывает стол и отправляет блюда → кухня отмечает «Готово» → официант получает уведомление →
касса открывает смену и принимает оплату.

Отключить консоль на сервере: `DOSTUK_SERVE_TEST_CONSOLE=false`.

## Запуск

### Локально

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
# PostgreSQL 16: роли dostuk (владелец) и dostuk_app (приложение), БД dostuk; Redis на 6379
alembic upgrade head
python -m app.cli bootstrap --org "Достук" --venue "Достук Центр" --slug dostuk-center \
  --owner-name "Владелец" --owner-email owner@dostuk.kg --owner-password 'надёжный-пароль'
DOSTUK_RUN_PUBLISHER_IN_API=true uvicorn app.main:app --reload   # без отдельного worker
# Swagger: http://localhost:8000/api/docs
```

### Тесты

```bash
pytest -q          # нужен PostgreSQL 16 с ролями dostuk / dostuk_app и БД dostuk_test, Redis
```
Адреса переопределяются `TEST_ADMIN_DATABASE_URL`, `TEST_DATABASE_URL`, `TEST_REDIS_URL`.

### Сервер (Docker Compose)

```bash
cp .env.example .env    # заполнить пароли, JWT-секрет, домены
docker compose up -d --build
docker compose run --rm api python -m app.cli bootstrap ...
```
Контейнеры: caddy, api (uvicorn, 2 воркера), worker (arq), postgres, redis, minio; `migrate`
выполняет `alembic upgrade head` перед стартом api/worker. CI — `.github/workflows/ci.yml`.

## Что осталось за рамками / допущения

* **Фискализация** — заглушка `billing.service.send_fiscal` (ККМ и способ передачи в ГНС не выбраны).
  Чеки копятся в `fiscal_receipts`, worker отправляет их с повторами.
* **Статусы из ТЗ официанта** самого ТЗ не было — заложена схема выше; меняется в одном месте.
* **Перенос из старой SQLite** — скрипт не написан: нужна схема текущей базы.
* Бэкапы (pgBackRest + WAL), Sentry и Uptime Kuma настраиваются на сервере, в коде их нет.
* Сайт-меню (Next.js) и Flutter-клиенты — отдельные репозитории; Dart-клиент генерируется из
  `/api/v1/openapi.json`.
