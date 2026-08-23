"""Fluxo de cadastro inicial dirigido por comando (`/cadastro`, TASK-060).

Fora do vocabulário fechado do `IntentInterpreter`: enquanto um cadastro
está em andamento, a mensagem seguinte do usuário é tratada diretamente
como resposta ao passo pendente, sem passar pela IA. Não lida com sessão de
banco nem com o Telegram — apenas muta o `User` recebido e devolve o texto
de resposta. Exceção pontual (TASK-072): o passo `username` recebe a
`AsyncSession` só para uma checagem antecipada de disponibilidade --
consulta de UX, não substitui a constraint `UNIQUE` do banco
(`uq_users_username`), que continua sendo a proteção real contra corrida.

Assíncrono desde a extensão da TASK-079 (webhook Telegram) -- único
chamador é `app.telegram.router`, então não há versão síncrona a manter.
"""

import re
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.intent.contracts import MISSION_SOURCE_CODES
from app.users.models import User

REGISTRATION_STEPS: Final[tuple[str, ...]] = (
    "username",
    "email",
    "favorite_stores",
    "preferred_categories",
)

_SKIP_WORDS = frozenset({"pular", "pula", "skip", "nenhum", "nenhuma", "-"})
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_NUMBERED_STORES = {
    "1": "kabum",
    "2": "pichau",
    "3": "terabyte",
    "4": "amazon",
    "5": "magalu",
    "6": "mercadolivre",
}
_ALL_STORES = frozenset({"7", "todo", "todos", "toda", "todas"})

# TASK-067: taxonomia real consolidada de Kabum, Pichau, Terabyte e
# Amazon.com.br (docs/tasks/TASK-067.md) — categoria entra na lista só se
# aparecer em pelo menos 2 das 4 lojas, para não herdar o catálogo
# genérico da Amazon (livros, moda, beleza etc.) sem correspondência nas
# outras 3 lojas especializadas em hardware/gamer.
_NUMBERED_CATEGORIES = {
    "1": "hardware",
    "2": "perifericos",
    "3": "computadores",
    "4": "notebooks",
    "5": "monitores",
    "6": "celulares",
    "7": "tv_audio",
    "8": "video_games",
    "9": "cadeiras_moveis",
    "10": "casa_inteligente",
    "11": "eletrodomesticos",
    "12": "cameras_drones",
    "13": "redes_conectividade",
    "14": "seguranca",
    "15": "geek_colecionaveis",
}
PREFERRED_CATEGORY_CODES: Final[frozenset[str]] = frozenset(
    _NUMBERED_CATEGORIES.values()
)
_ALL_CATEGORIES = frozenset({"16", "todo", "todos", "toda", "todas"})

_PROMPTS: Final[dict[str, str]] = {
    "username": "Vamos cadastrar você!\n\nQual nome de usuário você quer usar?",
    "email": (
        "Certo!\n\nAgora me informe seu e-mail.\n\n"
        'Se preferir deixar em branco, responda "pular".'
    ),
    "favorite_stores": (
        "🏪 Quais lojas você prefere?\n\n"
        "1 — Kabum\n2 — Pichau\n3 — Terabyte\n4 — Amazon\n5 — Magalu\n"
        "6 — Mercado Livre\n7 — Todas\n\n"
        "Digite os números separados por vírgula.\nExemplo: 1,2\n\n"
        "Para escolher todas, envie 7.\n"
        'Se não quiser definir agora, responda "pular".'
    ),
    "preferred_categories": (
        "🛒 Quais categorias você mais compra?\n\n"
        "1 — Hardware / Componentes de PC\n2 — Periféricos\n"
        "3 — Computadores / PC Gamer montado\n4 — Notebooks\n5 — Monitores\n"
        "6 — Celulares e Smartphones\n7 — TV, Áudio e Vídeo\n"
        "8 — Video Games e Consoles\n9 — Cadeiras e Móveis Gamer/Escritório\n"
        "10 — Casa Inteligente e Automação\n"
        "11 — Eletrodomésticos e Eletroportáteis\n12 — Câmeras e Drones\n"
        "13 — Redes e Conectividade\n14 — Segurança (câmeras, alarmes)\n"
        "15 — Geek e Colecionáveis\n16 — Todas\n\n"
        "Digite os números separados por vírgula.\nExemplo: 1,4,8\n\n"
        "Para escolher todas, envie 16.\n"
        'Se não quiser definir agora, responda "pular".'
    ),
}

_COMPLETION_MESSAGE = (
    "✅ Cadastro confirmado!\n\nAgora vamos criar sua senha.\n\n"
    "Use o link seguro que vou enviar abaixo."
)


class RegistrationError(ValueError):
    """Entrada inválida para o passo de cadastro atual."""


def start_registration(user: User) -> str:
    """Inicia (ou reinicia) o cadastro, sobrescrevendo passos anteriores."""
    first_step = REGISTRATION_STEPS[0]
    user.registration_step = first_step
    return _PROMPTS[first_step]


async def advance_registration(
    user: User, *, answer: str, session: AsyncSession
) -> str:
    """Aplica a resposta ao passo pendente e avança para o próximo."""
    step = user.registration_step
    if step not in REGISTRATION_STEPS:
        raise RegistrationError("no registration step in progress")

    skip = answer.strip().lower() in _SKIP_WORDS
    if step == "username":
        if skip:
            raise RegistrationError("nome de usuário é obrigatório, não pode pular")
        username = _validate_username(answer)
        await _ensure_username_available(username, session=session, user=user)
        user.username = username
    elif step == "email":
        user.email = None if skip else _validate_email(answer)
    elif step == "favorite_stores":
        user.favorite_stores = [] if skip else _parse_stores(answer)
    else:
        user.preferred_categories = [] if skip else _parse_categories(answer)

    next_index = REGISTRATION_STEPS.index(step) + 1
    if next_index >= len(REGISTRATION_STEPS):
        user.registration_step = None
        return _COMPLETION_MESSAGE

    next_step = REGISTRATION_STEPS[next_index]
    user.registration_step = next_step
    return _PROMPTS[next_step]


def _validate_username(raw: str) -> str:
    username = raw.strip()
    if not username or len(username) > 32:
        raise RegistrationError(
            "Esse nome de usuário precisa ter entre 1 e 32 caracteres."
        )
    if username.startswith("/") or " " in username:
        raise RegistrationError(
            'O nome de usuário não pode começar com "/" nem conter espaços.'
        )
    return username


async def _ensure_username_available(
    username: str, *, session: AsyncSession, user: User
) -> None:
    """TASK-072: checagem antecipada, só de UX -- se o nome já pertence a
    outra conta, mantém a pessoa no passo `username` com uma mensagem
    clara, em vez de deixar avançar e travar mais adiante. A constraint
    `UNIQUE` do banco (`uq_users_username`) continua ativa e é a proteção
    real contra corrida; esta consulta não a substitui nem a enfraquece."""
    taken = await session.scalar(
        select(User.id).where(User.username == username, User.id != user.id)
    )
    if taken is not None:
        raise RegistrationError(
            f'O nome de usuário "{username}" já está em uso.\n\nEscolha outro.'
        )


def _validate_email(raw: str) -> str:
    email = raw.strip()
    if len(email) > 254 or not _EMAIL_PATTERN.match(email):
        raise RegistrationError(
            'Esse e-mail não parece válido.\n\nTente novamente ou responda "pular".'
        )
    return email


def _parse_stores(raw: str) -> list[str]:
    tokens = [
        token.strip().lower() for token in re.split(r"[,\s]+", raw) if token.strip()
    ]
    if any(token in _ALL_STORES for token in tokens):
        return sorted(MISSION_SOURCE_CODES)
    stores = [
        _NUMBERED_STORES.get(token, token)
        for token in tokens
        if _NUMBERED_STORES.get(token, token) in MISSION_SOURCE_CODES
    ]
    if not stores:
        raise RegistrationError(
            "Não entendi as lojas escolhidas.\n\nUse os números de 1 a 6 "
            'separados por vírgula, 7 para todas ou responda "pular".'
        )
    return sorted(set(stores))


def _parse_categories(raw: str) -> list[str]:
    tokens = [
        token.strip().lower() for token in re.split(r"[,\s]+", raw) if token.strip()
    ]
    if any(token in _ALL_CATEGORIES for token in tokens):
        return sorted(_NUMBERED_CATEGORIES.values())
    categories = [
        _NUMBERED_CATEGORIES[token] for token in tokens if token in _NUMBERED_CATEGORIES
    ]
    if not categories:
        raise RegistrationError(
            "Não entendi as categorias escolhidas.\n\nUse os números de 1 a 15 "
            'separados por vírgula, 16 para todas ou responda "pular".'
        )
    return sorted(set(categories))
