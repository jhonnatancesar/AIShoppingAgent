"""Endpoint de vivacidade da aplicação."""

import asyncio
import logging
from functools import lru_cache
from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.database.session import create_database_engine

logger = logging.getLogger("app.health")


class HealthResponse(BaseModel):
    """Resposta estável da verificação de vivacidade."""

    status: Literal["ok"] = "ok"


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]


router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    operation_id="get_health",
    summary="Verificar saúde",
    description="Confirma que o processo da API está disponível.",
    response_description="Aplicação disponível.",
)
def get_health() -> HealthResponse:
    """Confirma que o processo da API está disponível."""
    return HealthResponse()


@lru_cache
def _readiness_engine() -> Engine:
    settings = get_settings()
    return create_database_engine(
        settings, connect_timeout_seconds=settings.readiness_timeout_seconds
    )


def _postgres_is_ready() -> None:
    with _readiness_engine().connect() as connection:
        connection.execute(text("SELECT 1"))


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse}},
    operation_id="get_readiness",
    summary="Verificar prontidão",
    description="Confirma a disponibilidade do PostgreSQL necessário à API.",
)
async def get_readiness(response: Response) -> ReadinessResponse:
    settings = get_settings()
    try:
        await asyncio.wait_for(
            asyncio.to_thread(_postgres_is_ready),
            timeout=settings.readiness_timeout_seconds,
        )
    except TimeoutError, SQLAlchemyError:
        logger.warning(
            "readiness_dependency_unavailable", extra={"dependency": "postgresql"}
        )
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(status="not_ready")
    return ReadinessResponse(status="ready")
