"""Resolução segura do IP do cliente por trás de proxy (validação de
segurança, Subtask 9).

PROD do GG Oferta recebe tráfego público através de um Cloudflare Tunnel
(`cloudflared`), que se conecta ao `api` só via loopback -- `compose.yaml`
liga a porta da API em `127.0.0.1` por padrão (`API_BIND_ADDRESS`), nunca
numa interface exposta à internet. Confirmado empiricamente contra um
túnel `cloudflared` real (`cloudflared tunnel --url`) apontado para um
servidor de loopback local: toda requisição que passa pelo túnel chega ao
processo local com `request.client.host == "127.0.0.1"` (o peer TCP é
sempre o processo `cloudflared`, nunca o visitante real) e carrega
`CF-Connecting-IP` com o IP real do visitante -- header que o próprio
edge do Cloudflare recusa deixar um cliente forjar (tentativa de enviar
`CF-Connecting-IP` manualmente resultou em "error code: 1000", a
requisição nem chegou à origem). `X-Forwarded-For` também chega com o IP
real, mas como último elemento de uma cadeia -- um cliente que também
envia esse header tem o valor dele MANTIDO e o IP real do Cloudflare
ANEXADO ao final (`"1.2.3.4,<ip-real>"`), então só o último elemento é
confiável; qualquer coisa antes dele é texto arbitrário do cliente.

A defesa real não é "o header em si é difícil de forjar" (isso é uma
propriedade do edge do Cloudflare, não do nosso processo) -- é que só
quem consegue abrir uma conexão TCP na porta da API pode apresentar
qualquer header. Como essa porta só aceita conexões de loopback, só
`cloudflared` (tráfego real, já vetado pelo Cloudflare) ou outro
processo do mesmo host (já confiável por definição -- se alguém tem
execução de código no host, um header forjado é o menor dos problemas)
conseguem chegar até aqui. Por isso `resolve_client_ip` só confia nos
headers de encaminhamento quando o peer TCP observado é loopback; uma
conexão direta de qualquer outra origem nunca é tratada como vinda do
túnel, mesmo que traga esses headers -- evita que uma mudança futura de
topologia (porta exposta em outra interface) vire um bypass silencioso.
"""

from fastapi import Request

_TRUSTED_PROXY_PEERS = frozenset({"127.0.0.1", "::1", "localhost"})
_CF_CONNECTING_IP_HEADER = "CF-Connecting-IP"
_X_FORWARDED_FOR_HEADER = "X-Forwarded-For"


def resolve_client_ip(request: Request) -> str:
    """IP do cliente real para decisões sensíveis (ex.: rate limit) que
    precisam de uma chave por-visitante, não por-processo-proxy.

    Só confia em `CF-Connecting-IP`/`X-Forwarded-For` quando a conexão
    TCP observada (`request.client.host`) é loopback -- ver docstring do
    módulo. Fora dessa condição (peer não-loopback, ou nenhum dos dois
    headers presente), devolve o peer TCP bruto -- nunca um valor vindo
    de um header não confiável."""
    peer = request.client.host if request.client is not None else None
    if peer not in _TRUSTED_PROXY_PEERS:
        return peer or "unknown"

    connecting_ip = request.headers.get(_CF_CONNECTING_IP_HEADER)
    if connecting_ip and connecting_ip.strip():
        return connecting_ip.strip()

    forwarded_for = request.headers.get(_X_FORWARDED_FOR_HEADER)
    if forwarded_for:
        # Cadeia de proxies: cada salto ANEXA seu peer observado ao final.
        # O valor confiável (o que o Cloudflare de fato observou) é
        # sempre o ÚLTIMO -- qualquer coisa antes dele é texto que o
        # próprio cliente pode ter enviado.
        hops = [hop.strip() for hop in forwarded_for.split(",") if hop.strip()]
        if hops:
            return hops[-1]

    return peer
