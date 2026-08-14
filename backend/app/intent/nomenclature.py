"""Detecção determinística de canonicalização não confiável (TASK-083).

Duas funções puras, sem IA e sem rede, usadas como sinal de acionamento
da verificação externa em `interpreter.py` -- nunca para gerar a resposta
canônica em si, só para decidir quando ela é suspeita o bastante para não
ser aceita sem checagem.

`check_known_family_contradiction` é um guardrail **pequeno e explícito**
(não um catálogo de produtos) para o caso real que motivou esta TASK:
a IA confundiu a linha Ryzen 7/Ryzen 9 dentro da própria família AMD X3D.
Cobre só isso -- não é a defesa principal.

`detect_unproven_enrichment` é a defesa geral: para uma entrada que já é
essencialmente só o código/modelo (sem outras palavras técnicas do
usuário), qualquer token técnico que a IA tenha acrescentado ao
`search_query` sem vir da mensagem original é tratado como inferido, não
confirmado -- independente de existir ou não uma regra cadastrada para
aquele produto específico.
"""

import re

_TOKEN_PATTERN = re.compile(r"[A-Z0-9]+")

# TASK-083: conectores genéricos do português informal usados para pedir um
# produto -- não são dados de produto, só permitem reconhecer quando a
# mensagem, tirando essas palavras, é essencialmente só o código/modelo.
_GENERIC_FILLER_WORDS = frozenset(
    {
        "QUERO",
        "QUERIA",
        "QRIA",
        "UM",
        "UMA",
        "UNS",
        "UMAS",
        "PROCURA",
        "PROCURO",
        "PROCURAR",
        "ACHE",
        "ACHAR",
        "ACHA",
        "ME",
        "MEU",
        "MINHA",
        "COMPRA",
        "COMPRAR",
        "POR",
        "FAVOR",
        "PFVR",
        "PF",
        "DE",
        "DO",
        "DA",
        "DOS",
        "DAS",
        "O",
        "A",
        "OS",
        "AS",
        "PRA",
        "PARA",
        "AI",
        "BORA",
        "VAMO",
        "VAMOS",
        "ESSE",
        "ESSA",
        "ESSES",
        "ESSAS",
        "AQUELE",
        "AQUELA",
        "TA",
        "AE",
        "NE",
    }
)

# TASK-083: guardrail mínimo e explícito -- não um catálogo de produtos.
# Cobre só o caso real que motivou a TASK (sufixo X3D da AMD): os números
# abaixo de cada conjunto são os únicos que já sabemos com certeza a que
# linha pertencem, direto da nomenclatura oficial do fabricante.
_AMD_X3D_RYZEN_7 = frozenset({"7800X3D", "9800X3D"})
_AMD_X3D_RYZEN_9 = frozenset({"7900X3D", "7950X3D", "9900X3D", "9950X3D"})


def _tokenize(text: str) -> list[str]:
    """Maiúsculas, sem acento relevante para códigos alfanuméricos, split
    em blocos alfanuméricos -- ignora separador (espaço/hífen/underscore),
    mesmo espírito tolerante a formato já usado no filtro da TASK-075."""
    return _TOKEN_PATTERN.findall(text.upper())


def _normalize_code(value: str) -> str:
    """Remove todo separador para comparar um código como um bloco só
    (ex.: "9800X3D" == "9800-X3D" == "9800 X3D")."""
    return "".join(_tokenize(value))


def check_known_family_contradiction(model: str, search_query: str) -> str | None:
    """Contradição determinística contra o guardrail mínimo (linha
    Ryzen 7 × Ryzen 9 da família X3D). `None` quando não há contradição
    conhecida -- isso nunca significa "confirmado seguro", só "este
    guardrail específico não achou problema"."""
    normalized_model = _normalize_code(model)
    query_tokens = set(_tokenize(search_query))
    has_ryzen_7 = "RYZEN" in query_tokens and "7" in query_tokens
    has_ryzen_9 = "RYZEN" in query_tokens and "9" in query_tokens
    if normalized_model in _AMD_X3D_RYZEN_7 and has_ryzen_9:
        return f"{model} pertence à linha Ryzen 7, não Ryzen 9"
    if normalized_model in _AMD_X3D_RYZEN_9 and has_ryzen_7:
        return f"{model} pertence à linha Ryzen 9, não Ryzen 7"
    return None


_WORD_PATTERN = re.compile(r"\S+")


def _is_filler_word(word: str) -> bool:
    return _normalize_code(word) in _GENERIC_FILLER_WORDS


def is_bare_model_input(raw_message: str, model: str) -> bool:
    """Verdadeiro quando `raw_message`, tirando conectores genéricos, é
    essencialmente só o código/modelo -- nenhuma outra palavra técnica do
    usuário (marca, categoria, característica) presente.

    Compara por palavra (separada por espaço) para reconhecer conector
    mesmo colado a pontuação (ex.: "pfvr," ou "um?"), mas junta as
    palavras restantes num bloco só antes de comparar ao modelo -- assim
    "9800-x3d" (hífen dentro do próprio código) e "9800x3d" são
    reconhecidos como o mesmo código, sem depender de onde o usuário pôs
    o separador."""
    words = _WORD_PATTERN.findall(raw_message.upper())
    significant_words = [word for word in words if not _is_filler_word(word)]
    return _normalize_code("".join(significant_words)) == _normalize_code(model)


def detect_unproven_enrichment(
    raw_message: str, model: str | None, search_query: str | None
) -> bool:
    """Verdadeiro quando a entrada é código/modelo isolado (ver
    `is_bare_model_input`) e `search_query` contém algum token técnico que
    não vem nem da mensagem original nem do próprio `model` -- marca,
    categoria ou família que a IA acrescentou por conta própria, sem
    nenhuma fonte determinística. Não depende de nenhuma tabela de
    produtos -- funciona para qualquer modelo, conhecido ou não.

    `False` sem levantar exceção quando `model`/`search_query` são `None`
    ou vazios -- nada para desconfiar quando não há modelo identificado."""
    if not model or not model.strip() or not search_query or not search_query.strip():
        return False
    if not is_bare_model_input(raw_message, model):
        return False
    raw_tokens = set(_tokenize(raw_message))
    model_tokens = set(_tokenize(model))
    query_tokens = _tokenize(search_query)
    extra_tokens = [
        token
        for token in query_tokens
        if token not in raw_tokens and token not in model_tokens
    ]
    return bool(extra_tokens)
