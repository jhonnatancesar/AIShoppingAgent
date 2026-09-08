"""Avisa o Coupon Worker (`AIShoppingAgent-cupom`, repositório e processo
separados, mesma máquina) quando o GG detecta atividade real de loja --
reaproveita o sinal HIGH_ACTIVITY já existente (`app.collection.cadence`),
nunca cria uma segunda regra de "promoção". Best-effort: o worker acelerar
a varredura (1h -> 30min) é uma otimização de cadência, nunca uma
dependência crítica da coleta -- falha aqui nunca pode interromper nem
atrasar o processamento normal da oferta.

Contrato do lado do worker (`control_server.py` daquele repositório):
`POST /control/promo` com `{"mode": "promotion", "window_start", "window_end"}`,
`Authorization: Bearer <AUTH_TOKEN dele>`. Sem `coupon_worker_control_url`/
`_token_file` configurados (`None`, default), esta função não faz nada --
aviso é opt-in, não fail-closed como AI/Search/Fetch via César Core."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)


async def notify_coupon_worker_high_activity(
    settings: Settings,
    *,
    now: datetime,
    client_factory: type[httpx.AsyncClient] = httpx.AsyncClient,
) -> None:
    if not settings.coupon_worker_control_url or not settings.coupon_worker_control_token_file:
        return
    try:
        token = settings.coupon_worker_control_token_file.read_text(
            encoding="utf-8"
        ).strip()
    except OSError:
        logger.warning("coupon_worker_control_token_unavailable")
        return
    if not token:
        logger.warning("coupon_worker_control_token_unavailable")
        return
    window_end = now + timedelta(
        minutes=settings.collection_high_activity_duration_minutes
    )
    endpoint = settings.coupon_worker_control_url.rstrip("/") + "/control/promo"
    try:
        async with client_factory(timeout=5, trust_env=False) as client:
            response = await client.post(
                endpoint,
                headers={"Authorization": "Bearer " + token},
                json={
                    "mode": "promotion",
                    "window_start": now.isoformat(),
                    "window_end": window_end.isoformat(),
                },
            )
        if response.status_code != 200:
            logger.warning(
                "coupon_worker_promo_notify_rejected",
                extra={"status_code": response.status_code},
            )
    except httpx.RequestError:
        # Worker fora do ar/desconfigurado -- não é uma falha da coleta,
        # só uma oportunidade de cadência perdida desta vez.
        logger.warning("coupon_worker_promo_notify_unreachable")
