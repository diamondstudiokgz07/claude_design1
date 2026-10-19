"""Idempotency-Key: повтор изменяющего запроса после обрыва сети не создаст второй заказ или оплату.

Ключ хранится в idempotency_keys (venue_id, key). Первый запрос резервирует ключ (processing),
успешный ответ (2xx) сохраняется и возвращается на повторы. Неуспешный — ключ освобождается.
"""

import hashlib
import json
import uuid
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import AppError, error_body, pick_lang
from app.core.models import IdempotencyKey
from app.core.security import decode_access_token, now
from app.db import tenant_session

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
STALE_PROCESSING = timedelta(seconds=60)


class IdempotencyMiddleware:
    def __init__(self, app: ASGIApp, prefix: str = "/api/v1"):
        self.app = app
        self.prefix = prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in MUTATING or not scope["path"].startswith(self.prefix):
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        raw_key = headers.get("idempotency-key")
        auth = headers.get("authorization", "")
        if not raw_key or not auth.lower().startswith("bearer "):
            await self.app(scope, receive, send)
            return
        lang = pick_lang(headers.get("accept-language"))
        try:
            key = uuid.UUID(raw_key)
            venue_id = uuid.UUID(decode_access_token(auth[7:].strip())["vid"])
        except (ValueError, AppError):
            # Неверный ключ/токен — пусть обработчик вернёт обычную ошибку авторизации/валидации
            await self.app(scope, receive, send)
            return

        body = b""
        more = True
        while more:
            msg = await receive()
            body += msg.get("body", b"")
            more = msg.get("more_body", False)
        req_hash = hashlib.sha256(scope["method"].encode() + scope["path"].encode() + b"\n" + body).hexdigest()

        async with tenant_session(venue_id) as s:
            inserted = await s.scalar(
                pg_insert(IdempotencyKey)
                .values(venue_id=venue_id, key=key, request_hash=req_hash, status="processing")
                .on_conflict_do_nothing()
                .returning(IdempotencyKey.key)
            )
            existing = None
            if inserted is None:
                existing = await s.scalar(
                    select(IdempotencyKey).where(IdempotencyKey.venue_id == venue_id, IdempotencyKey.key == key)
                )
                if existing and existing.status == "processing" and existing.created_at < now() - STALE_PROCESSING:
                    existing.created_at = now()
                    existing.request_hash = req_hash
                    existing = None  # зависший ключ — забираем себе

        if existing is not None:
            if existing.request_hash != req_hash:
                await _json(send, 422, error_body("idempotency_conflict", lang))
            elif existing.status == "processing":
                await _json(send, 409, error_body("request_in_progress", lang))
            else:
                await _json(send, existing.status_code or 200, existing.response, replay=True)
            return

        sent_body = False

        async def replay_receive() -> Message:
            nonlocal sent_body
            if not sent_body:
                sent_body = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        status = 500
        resp_headers: list = []
        chunks: list[bytes] = []

        async def capture_send(message: Message) -> None:
            nonlocal status, resp_headers
            if message["type"] == "http.response.start":
                status = message["status"]
                resp_headers = message.get("headers", [])
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, replay_receive, capture_send)
        finally:
            await self._finish(venue_id, key, status, resp_headers, b"".join(chunks))

    async def _finish(self, venue_id: uuid.UUID, key: uuid.UUID, status: int, headers: list, body: bytes) -> None:
        ctype = next((v.decode() for k, v in headers if k.decode().lower() == "content-type"), "")
        payload = None
        storable = 200 <= status < 300
        if storable:
            if not body:
                payload = None
            elif ctype.startswith("application/json"):
                payload = json.loads(body)
            else:
                storable = False
        async with tenant_session(venue_id) as s:
            if storable:
                row = await s.get(IdempotencyKey, (venue_id, key))
                if row:
                    row.status, row.status_code, row.response = "done", status, payload
            else:
                await s.execute(delete(IdempotencyKey).where(IdempotencyKey.venue_id == venue_id, IdempotencyKey.key == key))


async def _json(send: Send, status: int, payload, replay: bool = False) -> None:
    body = json.dumps(payload).encode() if payload is not None else b""
    headers = [(b"content-type", b"application/json")]
    if replay:
        headers.append((b"idempotent-replay", b"true"))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
