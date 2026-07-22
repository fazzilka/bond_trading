import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest
from starlette.exceptions import HTTPException as StarletteHTTPException

from bond_trading.api.router import router
from bond_trading.application.services.imports import ImportPreviewCache
from bond_trading.core.config import get_settings
from bond_trading.core.context import get_request_id
from bond_trading.core.logging import configure_logging
from bond_trading.core.metrics import MetricsMiddleware
from bond_trading.core.middleware import RequestContextMiddleware
from bond_trading.domain.errors import DomainError
from bond_trading.infrastructure.db.session import Database
from bond_trading.infrastructure.moex import MoexIssClient
from bond_trading.presentation.router import PRESENTATION_DIR
from bond_trading.presentation.router import router as presentation_router

logger = logging.getLogger(__name__)


def error_payload(code: str, message: str, details: object = None) -> dict[str, object]:
    return {
        "code": code,
        "message": message,
        "details": details,
        "request_id": get_request_id(),
    }


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.logging.level)
    database = Database(settings)
    http_client = httpx.AsyncClient(
        base_url=settings.moex.base_url,
        timeout=settings.moex.timeout_seconds,
        headers={"User-Agent": settings.moex.user_agent},
    )
    app.state.database = database
    app.state.moex_client = MoexIssClient(
        http_client,
        settings.moex,
        settings.business_timezone,
    )
    app.state.import_cache = ImportPreviewCache(settings.imports.preview_ttl_seconds)
    try:
        yield
    finally:
        await http_client.aclose()
        await database.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Track planned and current bond lot yields using MOEX ISS data.",
        lifespan=lifespan,
    )
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(router)
    app.include_router(presentation_router)
    app.mount("/static", StaticFiles(directory=PRESENTATION_DIR / "static"), name="static")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload("http_error", str(exc.detail)),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_payload("validation_error", "Request validation failed", exc.errors()),
        )

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_payload(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled request error", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=error_payload("internal_error", "Internal server error"),
        )

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/portfolio", status_code=307)

    @app.get("/metrics", tags=["operations"], summary="Prometheus metrics")
    async def metrics() -> Response:
        return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()


def run() -> None:
    uvicorn.run("bond_trading.main:app", host="0.0.0.0", port=8000, log_config=None)
