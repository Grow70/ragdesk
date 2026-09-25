"""Request tracing, logging, and consistent HTTP errors."""

import logging
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

LOGGER = logging.getLogger("ragdesk")


def configure_logging() -> None:
    if not LOGGER.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False


def error_response(
    request: Request,
    status: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error": {"code": code, "message": message},
            "request_id": request.state.request_id,
        },
        headers=headers,
    )


def install_http_behavior(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return error_response(
            request, exc.status_code, "HTTP_ERROR", message, headers=exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, _exc: RequestValidationError
    ) -> JSONResponse:
        return error_response(request, 422, "INVALID_REQUEST", "Invalid request")

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = uuid4().hex
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        except Exception as exc:
            LOGGER.error(
                "request_failed request_id=%s exception_type=%s",
                request_id,
                type(exc).__name__,
            )
            response = error_response(
                request, 500, "INTERNAL_ERROR", "Internal server error"
            )
        response.headers["X-Request-ID"] = request_id
        LOGGER.info(
            "request_completed request_id=%s method=%s path=%s status=%s",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
        )
        return response
