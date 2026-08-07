"""Ponto de entrada da aplicação FastAPI."""

from fastapi import FastAPI

from .core.config import get_settings
from .core.logging import configure_logging
from .core.request_logging import log_request
from .health.router import router as health_router
from .telegram.router import router as telegram_router

settings = get_settings()
configure_logging(settings.log_level)

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Agente inteligente de compras.",
)
app.middleware("http")(log_request)
app.include_router(health_router)
app.include_router(telegram_router)
