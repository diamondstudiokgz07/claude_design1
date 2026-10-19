import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.config import get_settings
from app.core.errors import AppError

_hasher = PasswordHasher()


def hash_secret(value: str) -> str:
    """Argon2 — для PIN и паролей."""
    return _hasher.hash(value)


def verify_secret(hashed: str | None, value: str) -> bool:
    if not hashed:
        return False
    try:
        return _hasher.verify(hashed, value)
    except (VerifyMismatchError, InvalidHashError):
        return False


def sha256(value: str) -> str:
    """Для высокоэнтропийных токенов (device/refresh/коды) достаточно SHA-256 — их можно искать по хешу."""
    return hashlib.sha256(value.encode()).hexdigest()


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def new_pairing_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))


def now() -> datetime:
    return datetime.now(UTC)


def create_access_token(
    *, staff_id: uuid.UUID, venue_id: uuid.UUID, session_id: uuid.UUID, device_id: uuid.UUID | None
) -> tuple[str, datetime]:
    s = get_settings()
    exp = now() + timedelta(minutes=s.access_token_ttl_min)
    payload: dict[str, Any] = {
        "sub": str(staff_id),
        "vid": str(venue_id),
        "sid": str(session_id),
        "did": str(device_id) if device_id else None,
        "exp": exp,
        "iat": now(),
        "typ": "access",
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm), exp


def decode_access_token(token: str) -> dict[str, Any]:
    s = get_settings()
    try:
        payload = jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
    except jwt.ExpiredSignatureError as e:
        raise AppError("token_expired", 401) from e
    except jwt.PyJWTError as e:
        raise AppError("unauthorized", 401) from e
    if payload.get("typ") != "access":
        raise AppError("unauthorized", 401)
    return payload
