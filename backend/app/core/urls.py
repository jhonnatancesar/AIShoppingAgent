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
