"""Единый формат ошибок: {code, message, details}; текст — на языке из Accept-Language (ky/ru)."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

MESSAGES: dict[str, dict[str, str]] = {
    "validation_error": {"ru": "Неверные данные запроса", "ky": "Суроо маалыматтары туура эмес"},
    "unauthorized": {"ru": "Требуется вход", "ky": "Кирүү талап кылынат"},
    "token_expired": {"ru": "Срок действия токена истёк", "ky": "Токендин мөөнөтү бүттү"},
    "forbidden": {"ru": "Недостаточно прав", "ky": "Укук жетишсиз"},
    "not_found": {"ru": "Не найдено", "ky": "Табылган жок"},
    "conflict": {"ru": "Конфликт данных", "ky": "Маалыматтар карама-каршы"},
    "device_required": {"ru": "Нужно привязанное устройство", "ky": "Байланган түзмөк керек"},
    "device_revoked": {"ru": "Устройство отозвано", "ky": "Түзмөк жокко чыгарылды"},
    "invalid_pairing_code": {"ru": "Код привязки неверен или истёк", "ky": "Байлоо коду туура эмес же мөөнөтү бүткөн"},
    "invalid_pin": {"ru": "Неверный PIN", "ky": "PIN туура эмес"},
    "pin_locked": {"ru": "Слишком много попыток, вход временно заблокирован", "ky": "Аракет өтө көп, кирүү убактылуу бөгөттөлдү"},
    "invalid_credentials": {"ru": "Неверный логин или пароль", "ky": "Логин же сырсөз туура эмес"},
    "totp_required": {"ru": "Нужен код подтверждения (2FA)", "ky": "Ырастоо коду керек (2FA)"},
    "invalid_totp": {"ru": "Неверный код подтверждения", "ky": "Ырастоо коду туура эмес"},
    "rate_limited": {"ru": "Слишком много запросов", "ky": "Суроолор өтө көп"},
    "table_busy": {"ru": "На столе уже есть открытый заказ", "ky": "Үстөлдө ачык буйрутма бар"},
    "order_not_editable": {"ru": "Заказ нельзя изменить в этом статусе", "ky": "Бул статуста буйрутманы өзгөртүүгө болбойт"},
    "invalid_transition": {"ru": "Недопустимый переход статуса", "ky": "Статусту мындай өзгөртүүгө болбойт"},
    "item_unavailable": {"ru": "Блюдо в стоп-листе", "ky": "Тамак стоп-тизмеде"},
    "invalid_modifiers": {"ru": "Неверный выбор модификаторов", "ky": "Модификаторлор туура эмес тандалды"},
    "shift_not_open": {"ru": "Смена не открыта", "ky": "Смена ачылган жок"},
    "shift_already_open": {"ru": "Смена уже открыта", "ky": "Смена ачык"},
    "overpayment": {"ru": "Сумма оплаты больше остатка", "ky": "Төлөм калдыктан көп"},
    "order_has_payments": {"ru": "По заказу уже есть оплаты", "ky": "Буйрутма боюнча төлөм бар"},
    "station_in_use": {"ru": "Укажите, куда передать блюда станции", "ky": "Станциянын тамактарын кайда өткөрүүнү көрсөтүңүз"},
    "idempotency_conflict": {"ru": "Ключ идемпотентности уже использован для другого запроса", "ky": "Идемпотенттүүлүк ачкычы башка суроо үчүн колдонулган"},
    "request_in_progress": {"ru": "Такой запрос уже выполняется", "ky": "Мындай суроо аткарылып жатат"},
    "station_forbidden": {"ru": "Эта станция вам недоступна", "ky": "Бул станция сизге жеткиликсиз"},
    "internal_error": {"ru": "Внутренняя ошибка сервера", "ky": "Сервердин ички катасы"},
}


def pick_lang(accept_language: str | None) -> str:
    if accept_language and accept_language.strip().lower().startswith("ky"):
        return "ky"
    return "ru"


def translate(code: str, lang: str) -> str:
    return MESSAGES.get(code, {}).get(lang) or MESSAGES.get(code, {}).get("ru") or code


class AppError(Exception):
    def __init__(self, code: str, status: int = 400, details: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.details = details or {}


class NotFound(AppError):
    def __init__(self, entity: str = "", **details: Any):
        super().__init__("not_found", 404, {"entity": entity, **details} if entity else details)


class Forbidden(AppError):
    def __init__(self, code: str = "forbidden", **details: Any):
        super().__init__(code, 403, details)


class Conflict(AppError):
    def __init__(self, code: str = "conflict", **details: Any):
        super().__init__(code, 409, details)


def error_body(code: str, lang: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"code": code, "message": translate(code, lang), "details": details or {}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        lang = pick_lang(request.headers.get("accept-language"))
        return JSONResponse(error_body(exc.code, lang, exc.details), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        lang = pick_lang(request.headers.get("accept-language"))
        errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return JSONResponse(error_body("validation_error", lang, {"errors": errors}), status_code=422)

    @app.exception_handler(IntegrityError)
    async def _integrity(request: Request, exc: IntegrityError):
        lang = pick_lang(request.headers.get("accept-language"))
        constraint = getattr(getattr(exc.orig, "__cause__", None), "constraint_name", None)
        return JSONResponse(error_body("conflict", lang, {"constraint": constraint}), status_code=409)
