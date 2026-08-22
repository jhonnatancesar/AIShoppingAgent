"""Validação CSRF de baixo nível (TASK-091, endurecimento de 2026-08-21/22).

Double-submit cookie: o cookie `aishopping_csrf` é legível por JS (não é
`httpOnly`) de propósito -- a SPA o ecoa no header `X-CSRF-Token` em toda
requisição mutável. Um site cross-origin não consegue ler o valor do
cookie (mesma-origem só) nem replicar o header customizado, então não
consegue montar um par header/cookie válido.

**Duas versões anteriores desta defesa foram corrigidas antes desta:**
um middleware genérico sobre `/api/v1/*` (amplo demais -- mascarava `404`
de rota inexistente como `403`, e teria protegido erroneamente um
endpoint futuro autenticado por Bearer/service token só por estar sob
esse prefixo); depois uma dependência no nível do router de
`web-sessions` (estreita demais -- só protegeria endpoints daquele
router; um endpoint futuro de missões/ofertas/admin em outro router não
herdaria nada).

**Desenho final: CSRF é propriedade da autenticação por `WebSession`, não
de nenhum router.** `validate_csrf` (esta função) é o núcleo de baixo
nível, com exatamente duas fontes de uso -- nunca uma terceira, para
nunca duplicar a defesa:

1. `app.webapp.router.create_web_session` (login) chama diretamente, via
   `Depends(validate_csrf)` só nessa rota -- ainda não existe
   `WebSession` nesse ponto (é o que cria a primeira), então não há como
   passar pela dependência de sessão; usa o cookie CSRF anônimo emitido
   pela casca da SPA antes do login.
2. `app.webapp.dependency.require_web_session` chama internamente, depois
   de resolver a sessão, só para métodos mutáveis -- é essa dependência
   (não o router) que qualquer endpoint autenticado por WebSession em
   qualquer módulo deve usar; a proteção CSRF vem embutida nela.
"""

import secrets

from fastapi import Request

from app.core.errors import ApiError

CSRF_COOKIE_NAME = "aishopping_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def validate_csrf(request: Request) -> None:
    """Núcleo de baixo nível: compara cookie e header, levanta `403` se
    ausentes ou diferentes. Não decide QUANDO aplicar (isso é
    responsabilidade de quem chama -- login sempre, `require_web_session`
    só em métodos mutáveis)."""
    cookie_value = request.cookies.get(CSRF_COOKIE_NAME)
    header_value = request.headers.get(CSRF_HEADER_NAME)
    if (
        not cookie_value
        or not header_value
        or not secrets.compare_digest(cookie_value, header_value)
    ):
        raise ApiError(
            status_code=403,
            code="csrf_invalid",
            message="Requisição inválida. Recarregue a página e tente novamente.",
        )
