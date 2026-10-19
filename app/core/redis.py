from redis.asyncio import Redis

from app.config import get_settings
from app.core.errors import AppError

_redis: Redis | None = None


def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
    _redis = None


async def hit_limit(key: str, limit: int, window_sec: int) -> int:
    """Фиксированное окно: увеличивает счётчик и возвращает текущее значение."""
    r = get_redis()
    pipe = r.pipeline()
    pipe.incr(key)
    pipe.expire(key, window_sec, nx=True)
    count, _ = await pipe.execute()
    return int(count)


async def rate_limit(key: str, limit: int, window_sec: int = 60) -> None:
    if await hit_limit(f"rl:{key}", limit, window_sec) > limit:
        raise AppError("rate_limited", 429)
