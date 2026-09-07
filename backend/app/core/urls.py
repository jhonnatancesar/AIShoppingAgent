"""Validação estrita de URLs públicas usadas por ofertas e mídia."""

from urllib.parse import urlsplit


def normalize_http_url(value: object) -> str | None:
    """Retorna HTTP/HTTPS absoluto sem credenciais, ou ``None``."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or (port is not None and not 1 <= port <= 65535)
    ):
        return None
    return text


def normalize_loopback_http_endpoint(value: object) -> str | None:
    """Aceita somente endpoint HTTP loopback com porta explícita."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    return text.rstrip("/")


def normalize_cesar_core_http_endpoint(value: object) -> str | None:
    """Aceita HTTP loopback OU `host.docker.internal`, com porta explícita.

    Usado só pelos clientes do César Core (AI/Search/Fetch,
    `app/ai_provider/cesar_core.py` e `app/search/cesar_core*.py`), que
    rodam tanto nativos no host (loopback -- `collection_worker`) quanto
    dentro do container `api` no mesmo host físico (`host.docker.internal`
    via `extra_hosts: host-gateway`, mesmo mecanismo já usado por
    `WINDOWS_OPS_AGENT_URL`/`ops_controller`, DEC-103/DEC-121). A lista de
    hosts é fechada -- nunca aceita um hostname arbitrário -- por isso é
    uma função própria, não uma flexibilização de
    `normalize_loopback_http_endpoint` (que continua estritamente loopback
    para `edge_cdp_url`, sem relação com o César Core).
    """
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() != "http"
        or parsed.hostname
        not in {"127.0.0.1", "localhost", "::1", "host.docker.internal"}
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    return text.rstrip("/")


def is_url_compatible_with_store(url: str, store_base_url: str) -> bool:
    """Aceita somente o host da loja ou um subdomínio desse host."""
    target = normalize_http_url(url)
    base = normalize_http_url(store_base_url)
    if target is None or base is None:
        return False
    target_host = urlsplit(target).hostname
    base_host = urlsplit(base).hostname
    if target_host is None or base_host is None:
        return False
    target_host = target_host.rstrip(".").lower()
    base_host = base_host.rstrip(".").lower()
    return target_host == base_host or target_host.endswith(f".{base_host}")
