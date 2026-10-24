"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.database import init_db
from app.errors import APIError
from app.routers import auth, explain, history
from app.schemas import HealthResponse
from app.services.rate_limiter import SlidingWindowRateLimiter

logger = logging.getLogger("linelens")
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Prepare the database before serving requests."""

    init_db()
    if settings.uses_default_jwt_secret:
        logger.warning("JWT_SECRET is using the development default; set it in .env.")
    app.state.rate_limiter = SlidingWindowRateLimiter(
        limit=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )
    yield


def _error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    headers: dict[str, str] | None = None,
    **extra: object,
) -> JSONResponse:
    body: dict[str, object] = {"code": code, "message": message}
    body.update(extra)
    return JSONResponse(
        status_code=status_code,
        content={"error": body},
        headers=headers,
    )


def create_app() -> FastAPI:
    """Build the FastAPI application."""

    app = FastAPI(
        title="LineLens API",
        version="1.0.0",
        description="Line-by-line explanations of Python code, grounded in AST analysis.",
        lifespan=lifespan,
    )

    @app.exception_handler(APIError)
    async def handle_api_error(_: Request, exc: APIError) -> JSONResponse:
        return _error_response(
            exc.status_code,
            exc.code,
            exc.message,
            headers=exc.headers,
            **exc.extra,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        fields = [
            {
                "field": ".".join(str(part) for part in error["loc"] if part != "body"),
                "message": error["msg"],
            }
            for error in exc.errors()
        ]
        return _error_response(
            422,
            "validation_error",
            "Invalid request data.",
            fields=fields,
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return _error_response(exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error", exc_info=exc)
        return _error_response(
            500,
            "internal_error",
            "Something went wrong. Please try again.",
        )

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        """Report that the web process is up."""

        return HealthResponse(status="ok")

    app.include_router(auth.router)
    app.include_router(explain.router)
    app.include_router(history.router)
    return app


app = create_app()
