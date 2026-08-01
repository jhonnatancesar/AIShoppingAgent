"""Endpoint de vivacidade da aplicação."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Resposta estável da verificação de vivacidade."""

    status: Literal["ok"] = "ok"


router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Verificar saúde")
def get_health() -> HealthResponse:
    """Confirma que o processo da API está disponível."""
    return HealthResponse()
