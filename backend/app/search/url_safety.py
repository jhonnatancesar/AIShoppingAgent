"""Política centralizada de segurança de URL para Search/Enrichment.

FASE E.3: a URL de um resultado de busca é input NÃO CONFIÁVEL (pode vir de
qualquer página indexada pela web aberta) e viaja, sem nenhuma transformação
de nenhuma camada intermediária, até um provider terceiro (Firecrawl Cloud,
via César Core → OmniRoute) -- e é usada tanto para decidir SE enriquecemos
uma URL quanto para o que fica registrado em `MarketPriceAssessment.evidence`
e no prompt de `_interpret_evidence`. Este módulo é o único lugar que decide
isso, para as duas finalidades, em vez de lógica duplicada em cada chamador.

Duas decisões distintas, nunca misturadas:

- ``is_safe_to_fetch``: a URL deve ser enviada ao César Core (e daí ao
  OmniRoute/Firecrawl)? Uma URL com um parâmetro de alta confiança (token,
  credencial, assinatura) NUNCA deve ser buscada -- mascarar o valor e
  seguir em frente produziria uma URL inválida e esconderia o problema
  (decisão explícita do usuário); a resposta correta é não buscar, nunca
  "buscar mesmo assim com o segredo escondido".
- ``safe_url_for_evidence``: qual representação da URL é segura para
  persistência (`evidence` JSONB) e para o prompt de IA -- sempre sem
  fragment, sempre sem os parâmetros de alta confiança (removidos, não
  mascarados), preservando qualquer outro parâmetro legítimo de e-commerce
  (id, sku, ref, página, categoria, código, chave curta etc.).

Isto é independente e complementar ao guard de SSRF (`reject_ssrf_target`
no César Core, `DEC-111`) -- SSRF é sobre PRA ONDE a URL aponta na rede;
isto aqui é sobre O QUE a URL carrega como dado.
"""

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Parâmetros de ALTA CONFIANÇA: nome quase nunca aparece por acidente em URL
# legítima de e-commerce. Deliberadamente NÃO inclui termos genéricos (id,
# sku, product, ref, page, category, code, key) -- decisão explícita do
# usuário, para não quebrar URL real de loja por falso positivo.
_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "access_token",
        "refresh_token",
        "oauth_token",
        "api_key",
        "apikey",
        "api-key",
        "authorization",
        "password",
        "passwd",
        "session",
        "session_id",
        "jwt",
    }
)

# URLs assinadas de provedores de nuvem -- a assinatura por si só já é o
# segredo (mascarar o valor não ajudaria; a URL só funciona com ela intacta).
_CLOUD_SIGNATURE_QUERY_KEYS = frozenset(
    {
        "x-amz-signature",
        "x-amz-credential",
        "x-amz-security-token",
        "x-goog-signature",
        "x-goog-credential",
    }
)

# Azure SAS não tem um único nome de parâmetro -- é caracterizado pela
# combinação de `sig` (assinatura) com `sv` (service version), específico o
# bastante para não colidir com uso genérico de `sig`/`sv` em outra loja.
_AZURE_SAS_MARKERS = frozenset({"sig", "sv"})


def _normalize_query_keys(query: str) -> frozenset[str]:
    return frozenset(key.lower() for key, _ in parse_qsl(query, keep_blank_values=True))


def _is_azure_sas(keys: frozenset[str]) -> bool:
    return _AZURE_SAS_MARKERS.issubset(keys)


def has_sensitive_query_data(url: str) -> bool:
    """``True`` se a URL carrega um parâmetro de alta confiança ou uma URL
    assinada de nuvem conhecida -- nunca por causa de parâmetro genérico."""
    keys = _normalize_query_keys(urlsplit(url).query)
    if keys & _SENSITIVE_QUERY_KEYS:
        return True
    if keys & _CLOUD_SIGNATURE_QUERY_KEYS:
        return True
    return _is_azure_sas(keys)


def is_safe_to_fetch(url: str) -> bool:
    """A URL pode ser enviada ao César Core/OmniRoute/Firecrawl para
    enriquecimento? Só considera dado sensível na URL -- alvo de rede
    (SSRF) é responsabilidade separada do César Core (`reject_ssrf_target`,
    `DEC-111`), não deste módulo."""
    return not has_sensitive_query_data(url)


def url_for_fetch_request(url: str) -> str:
    """URL efetivamente enviada ao César Core para enriquecimento: idêntica
    à original, exceto pelo ``#fragment`` (removido). Só chamar depois de
    confirmar ``is_safe_to_fetch(url)`` -- esta função nunca decide SE a URL
    deve ser buscada, só normaliza o que é enviado quando já se decidiu que
    sim. Fragment nunca é enviado pela rede por um cliente HTTP real (é
    semântica pura de user-agent, RFC 3986 §3.5), mas o valor bruto do campo
    ``url`` deste request é o que efetivamente viaja no corpo JSON até o
    César Core e pode ser logado por ele antes de qualquer requisição HTTP
    acontecer (defesa em profundidade -- o César Core também remove
    fragment do lado dele, FASE E.3)."""
    parsed = urlsplit(url)
    if not parsed.fragment:
        return url
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def safe_url_for_evidence(url: str) -> str:
    """Representação seguríssima da URL pra persistência (`evidence`) e pro
    prompt de IA: sem fragment, sem os parâmetros de alta confiança/URLs de
    assinatura (removidos, nunca mascarados) -- demais parâmetros legítimos
    preservados."""
    parsed = urlsplit(url)
    filtered_pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _SENSITIVE_QUERY_KEYS
        and key.lower() not in _CLOUD_SIGNATURE_QUERY_KEYS
    ]
    query_keys = _normalize_query_keys(parsed.query)
    if _is_azure_sas(query_keys):
        filtered_pairs = [
            (key, value)
            for key, value in filtered_pairs
            if key.lower() not in _AZURE_SAS_MARKERS
        ]
    safe_query = urlencode(filtered_pairs)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, safe_query, ""))


@dataclass(frozen=True, slots=True)
class RedactionResult:
    text: str
    redacted: bool


def redact_sensitive_query_values(text: str) -> RedactionResult:
    """Redige valores de parâmetros de alta confiança dentro de um texto
    livre (ex.: mensagem de exceção antes de persistir em `last_error`) --
    nunca assume que o texto é uma URL bem formada; procura o padrão
    ``chave=valor`` (delimitado por ``&``, espaço, aspas ou fim de string)
    caso a caso, sem regex genérica de PII."""
    all_keys = _SENSITIVE_QUERY_KEYS | _CLOUD_SIGNATURE_QUERY_KEYS | {"sig"}
    pattern = re.compile(
        r"(?i)\b(" + "|".join(re.escape(key) for key in all_keys) + r")=([^&\s\"'<>]+)"
    )
    redacted = False

    def _replace(match: re.Match[str]) -> str:
        nonlocal redacted
        redacted = True
        return f"{match.group(1)}=[REDACTED]"

    new_text = pattern.sub(_replace, text)
    return RedactionResult(text=new_text, redacted=redacted)
