"""FastAPI application factory."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import create_engine

from app.api.auth import router as auth_router
from app.api.documents import install_document_error_handler
from app.api.documents import router as document_router
from app.api.health import router as health_router
from app.api.knowledge_bases import install_kb_error_handler
from app.api.knowledge_bases import router as kb_router
from app.config import load_settings
from app.http import configure_logging, install_http_behavior


def create_app() -> FastAPI:
    settings = load_settings()
    configure_logging()
    engine = create_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        engine.dispose()

    app = FastAPI(title="Ragdesk API", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    install_http_behavior(app)
    install_kb_error_handler(app)
    install_document_error_handler(app)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(kb_router)
    app.include_router(document_router)
    return app
