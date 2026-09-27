"""Request tracing, logging, and consistent HTTP errors."""

import logging
import re
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm import sessionmaker
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.services import traces

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
    request.state.trace_error = code
    return JSONResponse(
        status_code=status,
        content={
            "error": {"code": code, "message": message},
            "request_id": request.state.request_id,
        },
        headers=headers,
    )


def install_http_behavior(app: FastAPI) -> None:
    app.state.trace_settings = traces.load_settings()

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
        trace = None
        if request.method == "POST" and re.fullmatch(
            r"/knowledge-bases/[^/]+/answers/?", request.url.path
        ):
            try:
                kb_id = UUID(request.url.path.split("/")[2])
            except ValueError:
                kb_id = None
            trace = traces.RequestTrace(
                request_id, kb_id, app.state.trace_settings.prices
            )
            request.state.answer_trace = trace
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
        if trace is not None:
            try:
                payload = trace.finish(
                    response.status_code, getattr(request.state, "trace_error", None)
                )
                await run_in_threadpool(
                    traces.persist, sessionmaker(app.state.engine), trace, payload
                )
                response.headers["X-Trace-Status"] = "stored"
            except Exception as exc:
                LOGGER.error(
                    "trace_unavailable request_id=%s exception_type=%s",
                    request_id,
                    type(exc).__name__,
                )
                response.headers["X-Trace-Status"] = "unavailable"
        response.headers["X-Request-ID"] = request_id
        LOGGER.info(
            "request_completed request_id=%s method=%s path=%s status=%s",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
        )
        return response
