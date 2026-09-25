"""FastAPI application factory."""

from fastapi import FastAPI

from app.api.health import router as health_router
from app.config import load_settings
from app.http import configure_logging, install_http_behavior


def create_app() -> FastAPI:
    settings = load_settings()
    configure_logging()
    app = FastAPI(title="Ragdesk API")
    app.state.settings = settings
    install_http_behavior(app)
    app.include_router(health_router)
    return app
