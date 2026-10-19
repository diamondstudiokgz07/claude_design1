from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="DOSTUK_", extra="ignore")

    env: str = "dev"
    # Приложение подключается ролью без BYPASSRLS; миграции — владельцем схемы.
    database_url: str = "postgresql+asyncpg://dostuk_app:dostuk_app@localhost:5432/dostuk"
    migrations_database_url: str = "postgresql+asyncpg://dostuk:dostuk@localhost:5432/dostuk"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: str = "change-me-in-production-please-32b"
    jwt_algorithm: str = "HS256"
    access_token_ttl_min: int = 15
    refresh_token_ttl_hours: int = 12
    owner_refresh_token_ttl_hours: int = 24 * 14
    pairing_code_ttl_min: int = 10

    pin_max_attempts_staff: int = 5
    pin_max_attempts_device: int = 20
    pin_lock_minutes: int = 15

    cors_origins: list[str] = ["https://menu.dostuk.kg"]
    public_cache_ttl_sec: int = 60
    public_rate_limit_per_min: int = 120

    # Хранилище файлов: "s3" (MinIO) или "local" (разработка/тесты)
    storage_backend: str = "local"
    storage_local_dir: str = "./data/files"
    s3_endpoint: str = "http://minio:9000"
    s3_bucket: str = "dostuk"
    s3_access_key: str = "minio"
    s3_secret_key: str = "minio-secret"
    files_public_base_url: str = "/files"

    outbox_poll_interval_sec: float = 0.5
    run_publisher_in_api: bool = False  # для локальной разработки без worker
    idempotency_ttl_hours: int = 48
    min_app_version: str = "3.0.0"
    serve_test_console: bool = True  # HTML-консоль на /app/ (выключить, когда будут Flutter-приложения)


@lru_cache
def get_settings() -> Settings:
    return Settings()
