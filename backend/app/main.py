"""Ponto de entrada da aplicação FastAPI."""

from fastapi import FastAPI

from .core.config import get_settings
from .health.router import router as health_router

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Agente inteligente de compras.",
)
app.include_router(health_router)
