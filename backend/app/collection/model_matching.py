"""Normalização e correspondência determinística de modelo/produto (TASK-075).

Extraído de `app.collection.orchestration` na TASK-083 para reuso pela
resolução de identidade de produto, sem duplicar lógica de matching.
Comportamento idêntico ao que já existia -- só mudou de módulo.
"""

import re
import unicodedata

# TASK-075: sufixos de variante oficiais de fabricante (NVIDIA: Ti/Super;
# AMD: XT/XTX/GRE) -- sempre indicam SKU/chip realmente diferente, nunca
# personalização de vendedor. OC/PRO/PLUS/MAX ficam de fora de propósito
# (uso inconsistente entre categorias, alto risco de falso-positivo).
STRONG_VARIANT_SUFFIXES = frozenset({"TI", "SUPER", "XT", "XTX", "GRE"})
SEPARATOR_PATTERN = re.compile(r"[\s\-_]+")
# TASK-083: separa só a fronteira dígito-run -> letra-run de um chunk já
# sem separador explícito (ex.: "9800X3D" -> "9800" + "X3D") -- nunca quebra
# um chunk todo em dígitos (ex.: "4070") nem um todo em letras (ex.: "TI"),
# então não afeta nenhum chunk que a TASK-075 já tratava como uma peça só.
_DIGIT_PREFIX_PATTERN = re.compile(r"^(\d+)([A-Z].*)$")


def normalize_for_matching(text: str) -> str:
    """TASK-075: maiúsculo, sem acento -- base para comparação determinística."""
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return without_accents.upper()


def model_search_pattern(model: str) -> re.Pattern[str]:
    """TASK-075: casa `model` no título tolerando espaço/hífen/underscore
    entre os pedaços (ex.: "RTX 4070 Ti" == "RTX-4070-Ti" == "RTX4070Ti"),
    exigindo limite alfanumérico nas duas pontas do trecho inteiro --
    resolve "9950X3D" x "9950X3D2"/"A9950X3D" ao mesmo tempo que tolera
    estilo de separador diferente.

    TASK-083: também tolera separador dentro de um código fundido sem
    espaço no próprio `model` (ex.: "9800X3D" == "9800-X3D" == "9800 X3D"),
    dividindo cada chunk na fronteira dígito->letra antes de montar o
    padrão -- sem isso, uma loja que escreve "9800-X3D" no título nunca
    bateria com o `model` "9800X3D" persistido."""
    normalized = normalize_for_matching(model)
    raw_chunks = [chunk for chunk in SEPARATOR_PATTERN.split(normalized) if chunk]
    chunks: list[str] = []
    for chunk in raw_chunks:
        digit_prefix_split = _DIGIT_PREFIX_PATTERN.match(chunk)
        if digit_prefix_split is not None:
            chunks.extend(digit_prefix_split.groups())
        else:
            chunks.append(chunk)
    core = r"[\s\-_]*".join(re.escape(part) for part in chunks)
    return re.compile(rf"(?<![A-Z0-9]){core}(?![A-Z0-9])")


def title_matches_model(model: str, title: str) -> bool:
    """TASK-075: False só quando o título comprovadamente não é o modelo
    pedido -- modelo ausente/colado a outro caractere (checagem via
    `model_search_pattern`), ou seguido de um sufixo de variante forte
    (`STRONG_VARIANT_SUFFIXES`) que a missão não pediu. Qualquer outra
    palavra depois do modelo (ex.: "OC", "Gaming", capacidade) não rejeita
    -- conservador por design."""
    normalized_title = normalize_for_matching(title)
    match = model_search_pattern(model).search(normalized_title)
    if match is None:
        return False
    remainder = normalized_title[match.end() :].lstrip(" \t-_")
    next_word_match = re.match(r"[A-Z0-9]+", remainder)
    if next_word_match is None:
        return True
    next_word = next_word_match.group(0)
    if next_word not in STRONG_VARIANT_SUFFIXES:
        return True
    model_tokens = set(SEPARATOR_PATTERN.split(normalize_for_matching(model)))
    return next_word in model_tokens


def same_code(a: str, b: str) -> bool:
    """TASK-083: verdadeiro quando `a` e `b` são o mesmo código inteiro,
    tolerando só diferença de separador (espaço/hífen/underscore) e
    maiúscula/minúscula -- ex.: "9800X3D" == "9800x3d" == "9800-X3D" ==
    "9800 X3D". Diferente de `title_matches_model`: aqui os dois lados
    precisam ser o código inteiro, não uma substring dentro de um título
    maior -- "AMD Ryzen 7 9800X3D" nunca é `same_code` de "9800X3D"."""
    return _strip_separators(a) == _strip_separators(b)


def _strip_separators(text: str) -> str:
    return SEPARATOR_PATTERN.sub("", normalize_for_matching(text))
