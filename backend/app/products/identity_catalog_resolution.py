"""TASK-137 (passo C) -- dúvida de pedido: consulta e registro (sem IA).

Chamado na criação/edição da missão, DENTRO da transação dela: só lê e grava uma
linha. A IA não entra aqui (regra do projeto: nunca IA com transação aberta); a
decisão vem depois, do worker (`identity_catalog_resolution_worker`).

- pedido já decidido (`resolved`) -> devolve a identidade na hora, sem IA;
- pedido novo com candidatos parecidos no catálogo -> registra `pending`;
- pedido sem candidato, ou já `none`/`ai_failed` -> nada muda.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.products.identity import MonitoringIdentity
from app.products.identity_catalog import CatalogEntrySnapshot
from app.products.identity_catalog_models import CatalogRequestResolution
from app.products.identity_catalog_request import (
    MAX_CANDIDATES,
    find_candidates,
    identity_from_entry,
    request_key,
)

_LABELS = "ABCDE"


def _candidate_rows(catalog, request_texts) -> list[dict]:
    return [
        {
            "label": _LABELS[index],
            "entry_id": str(candidate.entry.entry_id),
            "name": candidate.name,
            "brand": candidate.entry.brand,
        }
        for index, candidate in enumerate(
            find_candidates(catalog, request_texts, limit=min(MAX_CANDIDATES, 5))
        )
        if candidate.entry.entry_id is not None
    ]


def _chosen_identity(
    catalog: Sequence[CatalogEntrySnapshot], row: CatalogRequestResolution
) -> MonitoringIdentity | None:
    if row.status != "resolved" or row.chosen_entry_id is None:
        return None
    for entry in catalog:
        if entry.entry_id == row.chosen_entry_id:
            return identity_from_entry(entry)
    return None


def _new_row_values(
    primary_text: str, understood: dict, candidates: list[dict]
) -> dict:
    return {
        "request_key": request_key(primary_text),
        "request_text": primary_text[:500],
        "understood": understood,
        "candidates": candidates,
        "status": "pending",
    }


def lookup_or_register_sync(
    session: Session,
    catalog: Sequence[CatalogEntrySnapshot],
    *,
    primary_text: str,
    request_texts: Sequence[str],
    understood: dict,
) -> MonitoringIdentity | None:
    key = request_key(primary_text)
    row = session.scalar(
        select(CatalogRequestResolution).where(
            CatalogRequestResolution.request_key == key
        )
    )
    if row is not None:
        return _chosen_identity(catalog, row)
    candidates = _candidate_rows(catalog, request_texts)
    if candidates:
        session.execute(
            insert(CatalogRequestResolution)
            .values(**_new_row_values(primary_text, understood, candidates))
            .on_conflict_do_nothing(constraint="uq_catalog_request_resolutions_key")
        )
    return None


async def lookup_or_register(
    session: AsyncSession,
    catalog: Sequence[CatalogEntrySnapshot],
    *,
    primary_text: str,
    request_texts: Sequence[str],
    understood: dict,
) -> MonitoringIdentity | None:
    key = request_key(primary_text)
    row = await session.scalar(
        select(CatalogRequestResolution).where(
            CatalogRequestResolution.request_key == key
        )
    )
    if row is not None:
        return _chosen_identity(catalog, row)
    candidates = _candidate_rows(catalog, request_texts)
    if candidates:
        await session.execute(
            insert(CatalogRequestResolution)
            .values(**_new_row_values(primary_text, understood, candidates))
            .on_conflict_do_nothing(constraint="uq_catalog_request_resolutions_key")
        )
    return None
