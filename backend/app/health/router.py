"""Endpoint de vivacidade da aplicação."""

from typing import Literal

from fastapi import APIRouter, status
from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Resposta estável da verificação de vivacidade."""

    status: Literal["ok"] = "ok"


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
