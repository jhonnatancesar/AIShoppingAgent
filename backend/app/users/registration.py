"""Fluxo de cadastro inicial dirigido por comando (`/cadastro`, TASK-060).

Fora do vocabulário fechado do `IntentInterpreter`: enquanto um cadastro
está em andamento, a mensagem seguinte do usuário é tratada diretamente
como resposta ao passo pendente, sem passar pela IA. Não lida com sessão de
banco nem com o Telegram — apenas muta o `User` recebido e devolve o texto
de resposta.
"""

import re
from typing import Final

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
_MAX_CATEGORIES = 20
_MAX_CATEGORY_LENGTH = 64

_PROMPTS: Final[dict[str, str]] = {
    "username": "Vamos cadastrar você! Qual nome de usuário você quer usar?",
    "email": ('Certo! Agora seu e-mail (ou responda "pular" para deixar em branco).'),
    "favorite_stores": (
        "Quais lojas você prefere entre pichau, terabyte, amazon e kabum? "
        '(separe por vírgula, ou responda "pular")'
    ),
    "preferred_categories": (
        "Por último: quais categorias você mais compra (ex.: games, móveis)? "
        '(separe por vírgula, ou responda "pular")'
    ),
}

_COMPLETION_MESSAGE = "Cadastro concluído! Você já pode criar e consultar missões."


class RegistrationError(ValueError):
    """Entrada inválida para o passo de cadastro atual."""


def start_registration(user: User) -> str:
    """Inicia (ou reinicia) o cadastro, sobrescrevendo passos anteriores."""
    first_step = REGISTRATION_STEPS[0]
    user.registration_step = first_step
    return _PROMPTS[first_step]


def advance_registration(user: User, *, answer: str) -> str:
    """Aplica a resposta ao passo pendente e avança para o próximo."""
    step = user.registration_step
    if step not in REGISTRATION_STEPS:
        raise RegistrationError("no registration step in progress")

    skip = answer.strip().lower() in _SKIP_WORDS
    if step == "username":
        if skip:
            raise RegistrationError("nome de usuário é obrigatório, não pode pular")
        user.username = _validate_username(answer)
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
            "Nome de usuário inválido: precisa ter entre 1 e 32 caracteres."
        )
    if username.startswith("/") or " " in username:
        raise RegistrationError(
            'Nome de usuário não pode começar com "/" nem conter espaços.'
        )
    return username


def _validate_email(raw: str) -> str:
    email = raw.strip()
    if len(email) > 254 or not _EMAIL_PATTERN.match(email):
        raise RegistrationError('E-mail inválido. Tente de novo ou responda "pular".')
    return email


def _parse_stores(raw: str) -> list[str]:
    tokens = [
        token.strip().lower() for token in re.split(r"[,\s]+", raw) if token.strip()
    ]
    stores = [token for token in tokens if token in MISSION_SOURCE_CODES]
    if not stores:
        raise RegistrationError(
            "Não reconheci nenhuma loja. Use pichau, terabyte, amazon ou kabum, "
            'ou responda "pular".'
        )
    return sorted(set(stores))


def _parse_categories(raw: str) -> list[str]:
    categories = [token.strip() for token in raw.split(",") if token.strip()]
    if not categories:
        raise RegistrationError(
            'Não entendi nenhuma categoria. Tente de novo ou responda "pular".'
        )
    if len(categories) > _MAX_CATEGORIES:
        raise RegistrationError(
            f"Manda no máximo {_MAX_CATEGORIES} categorias por vez."
        )
    for category in categories:
        if len(category) > _MAX_CATEGORY_LENGTH:
            raise RegistrationError(
                f'"{category}" é muito longa (máximo {_MAX_CATEGORY_LENGTH} caracteres).'
            )
    return categories
