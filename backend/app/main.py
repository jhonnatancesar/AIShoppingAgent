"""Ponto de entrada da aplicação FastAPI."""

from fastapi import FastAPI

from .authentication.router import router as authentication_router
from .core.config import get_settings
from .core.errors import register_api_error_handler
from .core.logging import configure_logging
from .core.request_limits import MaxRequestBodyMiddleware
from .core.request_logging import log_request
from .health.router import router as health_router
from .observability.metrics import metrics_router
from .observability.tracing import configure_tracing
from .offers.router import router as offers_router
from .telegram.router import router as telegram_router
from .webapp.missions_router import router as webapp_missions_router
from .webapp.router import router as webapp_router
from .webapp.spa import register_spa

settings = get_settings()
configure_logging(
    settings.log_level,
    service_name="aishoppingagent-api",
    environment=settings.environment,
)
configure_tracing(settings, service_name="aishoppingagent-api")

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Agente inteligente de compras.",
)
app.add_middleware(MaxRequestBodyMiddleware, max_bytes=settings.max_request_body_bytes)
app.middleware("http")(log_request)
register_api_error_handler(app)
app.include_router(health_router)
app.include_router(metrics_router)
app.include_router(authentication_router)
app.include_router(offers_router)
app.include_router(telegram_router)
app.include_router(webapp_router)
app.include_router(webapp_missions_router)
# TASK-091: catch-all client-side de /app e /admin -- deve ser o último
# registrado, para nunca sombrear nenhuma rota de API acima.
register_spa(app, settings)
