import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app import models  # noqa: F401
from app.config import get_settings
from app.core import storage
from app.core.errors import install_error_handlers
from app.core.idempotency import IdempotencyMiddleware
from app.core.redis import close_redis, get_redis
from app.db import dispose_engine, get_engine
from app.modules.audit.router import router as audit_router
from app.modules.billing.router import router as billing_router
from app.modules.identity.router import auth as auth_router
from app.modules.identity.router import staff_router
from app.modules.menu.router import router as menu_router
from app.modules.orders.router import router as orders_router
from app.modules.printing.router import router as printing_router
from app.modules.public.router import router as public_router
from app.modules.realtime.hub import hub, publisher_loop
from app.modules.realtime.router import router as realtime_router
from app.modules.reports.router import router as reports_router
from app.modules.shifts.router import router as shifts_router
from app.modules.stations.router import router as stations_router
from app.modules.venues.router import router as venues_router

logging.basicConfig(level=logging.INFO, format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    storage.ensure_local_dir()
    await hub.start()
    publisher = asyncio.create_task(publisher_loop(s.outbox_poll_interval_sec)) if s.run_publisher_in_api else None
    yield
    if publisher:
        publisher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await publisher
    await hub.stop()
    await close_redis()
    await dispose_engine()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="Достук 3.0 API",
        version="3.0.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/v1/openapi.json",
    )
    install_error_handlers(app)
    app.add_middleware(IdempotencyMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Accept-Language"],
    )

    api = APIRouter(prefix="/api/v1")
    for r in (auth_router, staff_router, venues_router, menu_router, stations_router, orders_router, billing_router,
              shifts_router, reports_router, printing_router, audit_router, realtime_router, public_router):
        api.include_router(r)
    app.include_router(api)

    @app.get("/health", tags=["system"])
    async def health():
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        await get_redis().ping()
        return {"status": "ok"}

    if s.storage_backend != "s3":
        storage.ensure_local_dir()
        app.mount("/files", StaticFiles(directory=s.storage_local_dir), name="files")
    return app


app = create_app()
