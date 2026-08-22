"""Envelope de erro estável para endpoints `/api/v1` (`docs/development/api-conventions.md`).

`ApiError` existe porque o envelope documentado (`{"error": {"code",
"message", "details"}}`) não é o formato padrão do `HTTPException` do
FastAPI (que produz `{"detail": ...}`). Fundação da TASK-091 -- reutilizável
por qualquer endpoint `/api/v1` futuro da V1.2, não só pelos de sessão web.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    """Erro de aplicação com código estável, mensagem segura para o
    cliente e detalhes estruturados opcionais."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def register_api_error_handler(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )
