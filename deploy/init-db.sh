#!/bin/bash
# Роль приложения: без прав владельца и без BYPASSRLS — Row Level Security действует всегда.
# Права на таблицы выдаёт миграция 0002.
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
  CREATE ROLE dostuk_app LOGIN PASSWORD '${APP_DB_PASSWORD}' NOSUPERUSER NOBYPASSRLS;
  GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO dostuk_app;
SQL
