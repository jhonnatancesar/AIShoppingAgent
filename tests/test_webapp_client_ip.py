"""Resolução segura de IP do cliente atrás do Cloudflare Tunnel
(validação de segurança, Subtask 9) -- ver docstring de
`app.webapp.client_ip` para a prova empírica contra um túnel `cloudflared`
real: `request.client.host` chega sempre como `127.0.0.1`, `CF-Connecting-Ip`
carrega o IP real e não é forjável no próprio edge do Cloudflare,
`X-Forwarded-For` chega com o IP real ANEXADO ao final de qualquer valor
que o cliente já tenha enviado."""

from app.webapp.client_ip import resolve_client_ip
from starlette.requests import Request


def _request(*, peer: str | None, headers: dict[str, str] | None = None) -> Request:
    raw_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "headers": raw_headers,
        "client": (peer, 12345) if peer is not None else None,
    }
    return Request(scope)


def test_trusted_peer_with_cf_connecting_ip_returns_real_visitor_ip() -> None:
    request = _request(
        peer="127.0.0.1",
        headers={"CF-Connecting-IP": "2804:3d90:49:45d1::1"},
    )
    assert resolve_client_ip(request) == "2804:3d90:49:45d1::1"


def test_trusted_peer_without_cf_header_falls_back_to_last_x_forwarded_for_hop() -> None:
    request = _request(
        peer="127.0.0.1",
        headers={"X-Forwarded-For": "203.0.113.9"},
    )
    assert resolve_client_ip(request) == "203.0.113.9"


def test_client_spoofed_x_forwarded_for_is_appended_not_trusted_alone() -> None:
    """Reproduz exatamente o que o Cloudflare fez ao vivo: anexou o IP
    real ao final do valor forjado enviado pelo cliente. Só o último
    elemento é confiável."""
    request = _request(
        peer="127.0.0.1",
        headers={"X-Forwarded-For": "1.2.3.4, 2804:3d90:49:45d1:252c:aad4:e5ce:cf48"},
    )
    assert resolve_client_ip(request) == "2804:3d90:49:45d1:252c:aad4:e5ce:cf48"
    assert resolve_client_ip(request) != "1.2.3.4"


def test_non_loopback_peer_never_trusts_forwarded_headers() -> None:
    """Se a conexão TCP não veio do loopback, o túnel não é a única forma
    de alcançar esta porta -- headers de encaminhamento nunca são
    confiados, mesmo presentes; evita bypass se a topologia mudar."""
    request = _request(
        peer="203.0.113.55",
        headers={
            "CF-Connecting-IP": "9.9.9.9",
            "X-Forwarded-For": "9.9.9.9",
        },
    )
    assert resolve_client_ip(request) == "203.0.113.55"


def test_no_client_info_at_all_falls_back_to_unknown() -> None:
    request = _request(peer=None)
    assert resolve_client_ip(request) == "unknown"


def test_trusted_peer_without_any_forwarding_header_returns_peer_itself() -> None:
    request = _request(peer="127.0.0.1")
    assert resolve_client_ip(request) == "127.0.0.1"


def test_two_distinct_real_visitors_resolve_to_distinct_ips() -> None:
    first = _request(peer="127.0.0.1", headers={"CF-Connecting-IP": "198.51.100.1"})
    second = _request(peer="127.0.0.1", headers={"CF-Connecting-IP": "198.51.100.2"})
    assert resolve_client_ip(first) != resolve_client_ip(second)
