"""Vínculo Mission -> MonitoringItem e necessidade real de coleta por
(item, loja) -- TASK-112, fase 2.

Camada de dados/vínculo somente: nada aqui aciona nem é lido por nenhum
scheduler de coleta ainda (fase 3, fan-out/coleta compartilhada).
`MonitoringItemStore.is_enabled` só documenta a necessidade agregada,
mantida pelo lifecycle de missão abaixo -- ninguém lê para decidir o que
coletar nesta fase.

Sync/async espelhados, mesmo padrão do resto de `app.missions.service`
(`transition_mission`/`transition_mission_async`): a versão síncrona é
usada pelo `admin_router.py` (produção, Session síncrona), por
`app.privacy.service.deidentify_account` e por
`scripts/validate_collection_worker.py`; a assíncrona pelos fluxos
Telegram/Web de criação, transição e edição de identidade de missão.

`reconcile_mission_monitoring_item(_async)` é o ÚNICO ponto de entrada
para vincular/revincular/desvincular -- todo caller que pode alterar a
identidade relevante (`app.products.identity.resolve_monitoring_identity`)
de uma missão já criada passa por aqui, nunca duplica a lógica de
vínculo/desvínculo no próprio fluxo (ver auditoria completa de callers em
`docs/tasks/TASK-112.md`).
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.missions.models import (
    Mission,
    MissionCriteria,
    MissionMonitoringItem,
    MissionProductSelection,
    MissionSource,
    MissionStatus,
    MonitoringItem,
    MonitoringItemStore,
    VariantSelectionMode,
)
from app.products.identity import (
    MONITORING_KEY_VERSION,
    MonitoringIdentity,
    resolve_monitoring_identity,
    resolve_monitoring_identity_for_family,
    resolve_monitoring_identity_for_resolved_product,
)
from app.products.models import Product

_MONITORING_ITEM_KEY_CONSTRAINT = "uq_monitoring_items_monitoring_key"


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None)


def _canonical_identity_payload(identity: MonitoringIdentity) -> dict:
    return {
        "scope": identity.scope.value,
        "category": identity.category,
        "brand": identity.brand,
        "family": identity.family,
        "model": identity.model,
        "variant": identity.variant,
        "attributes": dict(identity.attributes),
    }


def _criteria_identity_text(criteria: MissionCriteria) -> str:
    return f"{criteria.search_query} {criteria.model}" if criteria.model else criteria.search_query


def _sorted_store_ids(store_ids: Sequence[UUID]) -> list[UUID]:
    """TASK-112 (correção de corrida): ordem determinística e estável
    entre TODAS as transações -- qualquer transação que precise travar
    mais de uma linha de `MonitoringItemStore` sempre as adquire na MESMA
    ordem (por `store_id`), então duas transações concorrentes tocando
    lojas sobrepostas nunca esperam uma pela outra em ordens opostas
    (deadlock)."""
    return sorted(store_ids)


# ---------------------------------------------------------------------------
# Resolver-ou-criar MonitoringItem -- mesmo padrão de concorrência já
# comprovado em produção (`_resolve_offer`, app.collection.orchestration,
# TASK-079): tenta achar; se não achar, tenta inserir dentro de um
# savepoint; se a inserção colidir (dois usuários concorrentes resolvendo
# a mesma monitoring_key), lê a linha que ganhou a corrida -- nunca dois
# MonitoringItem para a mesma chave.
# ---------------------------------------------------------------------------


def resolve_or_create_monitoring_item(
    session: Session, identity: MonitoringIdentity, *, now: datetime
) -> MonitoringItem:
    item = session.scalar(
        select(MonitoringItem).where(MonitoringItem.monitoring_key == identity.monitoring_key)
    )
    if item is not None:
        return item
    try:
        with session.begin_nested():
            item = MonitoringItem(
                monitoring_key=identity.monitoring_key,
                identity_version=MONITORING_KEY_VERSION,
                canonical_identity=_canonical_identity_payload(identity),
                created_at=now,
                updated_at=now,
            )
            session.add(item)
            session.flush()
    except IntegrityError as error:
        if _constraint_name(error) != _MONITORING_ITEM_KEY_CONSTRAINT:
            raise
        item = session.scalar(
            select(MonitoringItem).where(MonitoringItem.monitoring_key == identity.monitoring_key)
        )
        if item is None:
            raise
    return item


async def resolve_or_create_monitoring_item_async(
    session: AsyncSession, identity: MonitoringIdentity, *, now: datetime
) -> MonitoringItem:
    item = await session.scalar(
        select(MonitoringItem).where(MonitoringItem.monitoring_key == identity.monitoring_key)
    )
    if item is not None:
        return item
    try:
        async with session.begin_nested():
            item = MonitoringItem(
                monitoring_key=identity.monitoring_key,
                identity_version=MONITORING_KEY_VERSION,
                canonical_identity=_canonical_identity_payload(identity),
                created_at=now,
                updated_at=now,
            )
            session.add(item)
            await session.flush()
    except IntegrityError as error:
        if _constraint_name(error) != _MONITORING_ITEM_KEY_CONSTRAINT:
            raise
        item = await session.scalar(
            select(MonitoringItem).where(MonitoringItem.monitoring_key == identity.monitoring_key)
        )
        if item is None:
            raise
    return item


# ---------------------------------------------------------------------------
# Necessidade agregada por (item, loja) -- funções internas de baixo nível,
# reaproveitadas tanto pelo lifecycle de transição (ACTIVATE/RESUME/PAUSE/
# CANCEL/COMPLETE/EXPIRE) quanto pelo reconcile de identidade.
# ---------------------------------------------------------------------------


def _activate_item_stores(
    session: Session, *, monitoring_item_id: UUID, store_ids: Sequence[UUID], now: datetime
) -> None:
    """Sempre incondicional: religa (ou cria, na primeira vez) sem nunca
    resetar `next_run_at`/backoff de uma loja já conhecida -- reaproveita
    o estado existente. Correto mesmo sob corrida com um `deactivate`
    concorrente na mesma linha (ver `_deactivate_item_stores_if_unneeded`):
    o UPSERT do Postgres serializa com o `SELECT ... FOR UPDATE` de lá --
    não importa qual dos dois roda primeiro, o resultado final é sempre
    `True`, porque esta chamada só acontece quando ESTA missão de fato
    precisa da loja agora."""
    for store_id in _sorted_store_ids(store_ids):
        statement = (
            postgresql_insert(MonitoringItemStore)
            .values(
                monitoring_item_id=monitoring_item_id,
                store_id=store_id,
                is_enabled=True,
                next_run_at=now,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=[
                    MonitoringItemStore.monitoring_item_id,
                    MonitoringItemStore.store_id,
                ],
                set_={"is_enabled": True, "updated_at": now},
            )
        )
        session.execute(statement)


async def _activate_item_stores_async(
    session: AsyncSession, *, monitoring_item_id: UUID, store_ids: Sequence[UUID], now: datetime
) -> None:
    for store_id in _sorted_store_ids(store_ids):
        statement = (
            postgresql_insert(MonitoringItemStore)
            .values(
                monitoring_item_id=monitoring_item_id,
                store_id=store_id,
                is_enabled=True,
                next_run_at=now,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=[
                    MonitoringItemStore.monitoring_item_id,
                    MonitoringItemStore.store_id,
                ],
                set_={"is_enabled": True, "updated_at": now},
            )
        )
        await session.execute(statement)


def _other_active_mission_needs_store(
    session: Session, *, monitoring_item_id: UUID, store_id: UUID, excluding_mission_id: UUID
) -> bool:
    return bool(
        session.scalar(
            select(
                exists(
                    select(MissionMonitoringItem.mission_id)
                    .join(Mission, Mission.id == MissionMonitoringItem.mission_id)
                    .join(
                        MissionSource,
                        MissionSource.mission_id == MissionMonitoringItem.mission_id,
                    )
                    .where(
                        MissionMonitoringItem.monitoring_item_id == monitoring_item_id,
                        MissionMonitoringItem.mission_id != excluding_mission_id,
                        Mission.status == MissionStatus.ACTIVE,
                        MissionSource.store_id == store_id,
                    )
                )
            )
        )
    )


async def _other_active_mission_needs_store_async(
    session: AsyncSession,
    *,
    monitoring_item_id: UUID,
    store_id: UUID,
    excluding_mission_id: UUID,
) -> bool:
    return bool(
        await session.scalar(
            select(
                exists(
                    select(MissionMonitoringItem.mission_id)
                    .join(Mission, Mission.id == MissionMonitoringItem.mission_id)
                    .join(
                        MissionSource,
                        MissionSource.mission_id == MissionMonitoringItem.mission_id,
                    )
                    .where(
                        MissionMonitoringItem.monitoring_item_id == monitoring_item_id,
                        MissionMonitoringItem.mission_id != excluding_mission_id,
                        Mission.status == MissionStatus.ACTIVE,
                        MissionSource.store_id == store_id,
                    )
                )
            )
        )
    )


def _deactivate_item_stores_if_unneeded(
    session: Session,
    *,
    monitoring_item_id: UUID,
    store_ids: Sequence[UUID],
    excluding_mission_id: UUID,
    now: datetime,
) -> None:
    """Desliga `is_enabled` de `(item, loja)` só quando NENHUMA outra
    missão `ACTIVE` vinculada ao mesmo item ainda exige aquela loja.
    Nunca apaga a linha nem o histórico -- só para de ser considerada
    devida.

    Correção da corrida (TASK-112, fase 2 revisão): serialização via
    banco (`SELECT ... FOR UPDATE`), não mutex em memória. A linha é
    travada **antes** de reconsultar "alguém mais ainda precisa" -- sob
    READ COMMITTED (default do Postgres/psycopg aqui), cada nova consulta
    dentro da transação enxerga o que já foi commitado por quem quer que
    tenha acabado de liberar essa mesma trava, então a checagem feita
    DEPOIS de adquirir o lock é sempre a mais atual possível. Ordena as
    lojas (`_sorted_store_ids`) para nunca haver deadlock quando mais de
    uma linha precisa ser travada na mesma transação.
    """
    for store_id in _sorted_store_ids(store_ids):
        row = session.get(
            MonitoringItemStore, (monitoring_item_id, store_id), with_for_update=True
        )
        if row is None or not row.is_enabled:
            continue
        if _other_active_mission_needs_store(
            session,
            monitoring_item_id=monitoring_item_id,
            store_id=store_id,
            excluding_mission_id=excluding_mission_id,
        ):
            continue
        row.is_enabled = False
        row.updated_at = now


async def _deactivate_item_stores_if_unneeded_async(
    session: AsyncSession,
    *,
    monitoring_item_id: UUID,
    store_ids: Sequence[UUID],
    excluding_mission_id: UUID,
    now: datetime,
) -> None:
    for store_id in _sorted_store_ids(store_ids):
        row = await session.get(
            MonitoringItemStore, (monitoring_item_id, store_id), with_for_update=True
        )
        if row is None or not row.is_enabled:
            continue
        if await _other_active_mission_needs_store_async(
            session,
            monitoring_item_id=monitoring_item_id,
            store_id=store_id,
            excluding_mission_id=excluding_mission_id,
        ):
            continue
        row.is_enabled = False
        row.updated_at = now


# ---------------------------------------------------------------------------
# Lifecycle (chamado de dentro de transition_mission(_async) para TODA
# transição que entra/sai de ACTIVE) -- cobre uniformemente qualquer
# chamador (admin, Telegram, Web), não só a criação.
# ---------------------------------------------------------------------------


def activate_monitoring_item_stores(session: Session, *, mission_id: UUID, now: datetime) -> None:
    """ACTIVATE/RESUME: reativa a necessidade de coleta para cada loja que
    a missão seleciona."""
    link = session.get(MissionMonitoringItem, mission_id)
    if link is None:
        return
    store_ids = session.scalars(
        select(MissionSource.store_id).where(MissionSource.mission_id == mission_id)
    ).all()
    _activate_item_stores(
        session, monitoring_item_id=link.monitoring_item_id, store_ids=store_ids, now=now
    )


async def activate_monitoring_item_stores_async(
    session: AsyncSession, *, mission_id: UUID, now: datetime
) -> None:
    link = await session.get(MissionMonitoringItem, mission_id)
    if link is None:
        return
    store_ids = (
        await session.scalars(
            select(MissionSource.store_id).where(MissionSource.mission_id == mission_id)
        )
    ).all()
    await _activate_item_stores_async(
        session, monitoring_item_id=link.monitoring_item_id, store_ids=store_ids, now=now
    )


def deactivate_monitoring_item_stores_if_unneeded(
    session: Session, *, mission_id: UUID, now: datetime
) -> None:
    """PAUSE/CANCEL/COMPLETE/EXPIRE (saída de ACTIVE)."""
    link = session.get(MissionMonitoringItem, mission_id)
    if link is None:
        return
    store_ids = session.scalars(
        select(MissionSource.store_id).where(MissionSource.mission_id == mission_id)
    ).all()
    _deactivate_item_stores_if_unneeded(
        session,
        monitoring_item_id=link.monitoring_item_id,
        store_ids=store_ids,
        excluding_mission_id=mission_id,
        now=now,
    )


async def deactivate_monitoring_item_stores_if_unneeded_async(
    session: AsyncSession, *, mission_id: UUID, now: datetime
) -> None:
    link = await session.get(MissionMonitoringItem, mission_id)
    if link is None:
        return
    store_ids = (
        await session.scalars(
            select(MissionSource.store_id).where(MissionSource.mission_id == mission_id)
        )
    ).all()
    await _deactivate_item_stores_if_unneeded_async(
        session,
        monitoring_item_id=link.monitoring_item_id,
        store_ids=store_ids,
        excluding_mission_id=mission_id,
        now=now,
    )


# ---------------------------------------------------------------------------
# Identidade EFETIVA da missão (TASK-112, correção de precedência) --
# nunca deriva só de texto quando existe algo mais específico já
# resolvido pela TASK-097. Ordem:
#   1. variante de Product explicitamente SELECIONADA (`MissionProductSelection`,
#      `VariantSelectionMode.SELECTED`, exatamente uma) -- fonte mais
#      específica possível, inclusive mais específica que o texto
#      original (ex.: usuário escolheu 256GB depois de pedir só
#      "iPhone 17"). Duas ou mais variantes selecionadas não mapeiam para
#      uma única monitoring_key -- cai para o texto (fail-closed, nunca
#      escolhe uma arbitrariamente).
#   2. texto (`search_query`/`model`) -- continua a fonte certa enquanto
#      for a informação mais específica disponível (`SPECIFIC_PRODUCT` já
#      resolvido por texto, `PRODUCT_FAMILY`/`GENERIC_CATEGORY` ainda sem
#      variante escolhida, `VariantSelectionMode.ALL`/`PENDING`).
# As duas fontes convergem no MESMO algoritmo/formato de chave
# (`app.products.identity._build_monitoring_identity`) -- nunca dois
# jeitos diferentes de calcular monitoring_key.
# ---------------------------------------------------------------------------


def _selected_product_identity(
    session: Session, *, mission_id: UUID, criteria: MissionCriteria
) -> MonitoringIdentity | None:
    if criteria.variant_selection_mode is not VariantSelectionMode.SELECTED:
        return None
    product_ids = session.scalars(
        select(MissionProductSelection.product_id).where(
            MissionProductSelection.mission_id == mission_id
        )
    ).all()
    if len(product_ids) != 1:
        return None
    product = session.get(Product, product_ids[0])
    if product is None or product.identity_key is None:
        return None
    return resolve_monitoring_identity_for_resolved_product(
        category=product.category,
        brand=product.brand,
        family=product.family,
        model=product.model,
        variant=product.variant,
        attributes=product.attributes,
    )


async def _selected_product_identity_async(
    session: AsyncSession, *, mission_id: UUID, criteria: MissionCriteria
) -> MonitoringIdentity | None:
    if criteria.variant_selection_mode is not VariantSelectionMode.SELECTED:
        return None
    product_ids = (
        await session.scalars(
            select(MissionProductSelection.product_id).where(
                MissionProductSelection.mission_id == mission_id
            )
        )
    ).all()
    if len(product_ids) != 1:
        return None
    product = await session.get(Product, product_ids[0])
    if product is None or product.identity_key is None:
        return None
    return resolve_monitoring_identity_for_resolved_product(
        category=product.category,
        brand=product.brand,
        family=product.family,
        model=product.model,
        variant=product.variant,
        attributes=product.attributes,
    )


def _effective_identity(
    session: Session, *, mission_id: UUID, criteria: MissionCriteria
) -> MonitoringIdentity | None:
    """Ordem de precedência (TASK-112, correções sucessivas):
    1. variante de `Product` SELECIONADA (mais específica possível);
    2. `VariantSelectionMode.ALL` -- "qualquer variante desta família",
       escopo `FAMILY`, escolha deliberada do usuário, nunca ambiguidade;
    3. texto (`search_query`/`model`), escopo `SPECIFIC` -- só enquanto
       for a informação mais específica disponível (cobre `SPECIFIC_
       PRODUCT` já resolvido por texto e `PENDING`/`GENERIC_CATEGORY`,
       que continuam fail-closed, como sempre)."""
    selected = _selected_product_identity(session, mission_id=mission_id, criteria=criteria)
    if selected is not None:
        return selected
    if criteria.variant_selection_mode is VariantSelectionMode.ALL:
        return resolve_monitoring_identity_for_family(_criteria_identity_text(criteria))
    return resolve_monitoring_identity(_criteria_identity_text(criteria))


async def _effective_identity_async(
    session: AsyncSession, *, mission_id: UUID, criteria: MissionCriteria
) -> MonitoringIdentity | None:
    selected = await _selected_product_identity_async(
        session, mission_id=mission_id, criteria=criteria
    )
    if selected is not None:
        return selected
    if criteria.variant_selection_mode is VariantSelectionMode.ALL:
        return resolve_monitoring_identity_for_family(_criteria_identity_text(criteria))
    return resolve_monitoring_identity(_criteria_identity_text(criteria))


# ---------------------------------------------------------------------------
# Ponto único de entrada para vincular/revincular/desvincular (TASK-112,
# fase 2 revisão) -- chamado por todo caller que pode alterar a
# identidade relevante de uma missão já existente, ou criá-la. Nunca
# decide por conta própria se a missão está "ativa para fins de coleta":
# quem chama informa (`mission_is_active`), porque isso às vezes diverge
# do valor literal de `Mission.status` (ex.: `deidentify_account` força a
# agenda desligada sem necessariamente mudar o status da missão).
# ---------------------------------------------------------------------------


def reconcile_mission_monitoring_item(
    session: Session,
    *,
    mission_id: UUID,
    criteria: MissionCriteria,
    mission_is_active: bool,
    now: datetime,
) -> MonitoringItem | None:
    """Recalcula o vínculo da missão a partir da identidade ATUAL de
    `criteria` e devolve o `MonitoringItem` vinculado (ou `None`).

    - mesma identidade de antes (inclusive `None` -> `None`): idempotente,
      só garante que as stores atuais estão corretas se a missão está
      ativa (cobre o caso de `MissionSource` ter mudado sem a identidade
      mudar).
    - unresolved -> resolved: cria/acha o `MonitoringItem` e vincula.
    - item A -> item B: desvincula de A, vincula a B; A continua
      existindo, suas stores só desativam se nenhuma outra missão ativa
      vinculada a A ainda precisar delas (nunca em cascata/imediato só
      por causa dessa missão ter saído).
    - resolved -> unresolved (ex.: deidentificação): remove o vínculo com
      segurança, mesma reavaliação das stores do item antigo.

    Nunca apaga `MonitoringItem`/`MonitoringItemStore`/histórico -- só
    vínculo e `is_enabled`.
    """
    identity = _effective_identity(session, mission_id=mission_id, criteria=criteria)
    current_link = session.get(MissionMonitoringItem, mission_id, with_for_update=True)
    current_item_id = current_link.monitoring_item_id if current_link is not None else None

    target_item: MonitoringItem | None = None
    if identity is not None:
        target_item = resolve_or_create_monitoring_item(session, identity, now=now)
    target_item_id = target_item.id if target_item is not None else None

    store_ids = session.scalars(
        select(MissionSource.store_id).where(MissionSource.mission_id == mission_id)
    ).all()

    if target_item_id == current_item_id:
        if mission_is_active and current_item_id is not None:
            _activate_item_stores(
                session, monitoring_item_id=current_item_id, store_ids=store_ids, now=now
            )
        return target_item

    if current_link is not None:
        session.delete(current_link)
        session.flush()
    if target_item is not None:
        session.add(
            MissionMonitoringItem(
                mission_id=mission_id, monitoring_item_id=target_item.id, created_at=now
            )
        )
        session.flush()
        if mission_is_active:
            _activate_item_stores(
                session, monitoring_item_id=target_item.id, store_ids=store_ids, now=now
            )
    if current_item_id is not None:
        _deactivate_item_stores_if_unneeded(
            session,
            monitoring_item_id=current_item_id,
            store_ids=store_ids,
            excluding_mission_id=mission_id,
            now=now,
        )
    return target_item


async def reconcile_mission_monitoring_item_async(
    session: AsyncSession,
    *,
    mission_id: UUID,
    criteria: MissionCriteria,
    mission_is_active: bool,
    now: datetime,
) -> MonitoringItem | None:
    identity = await _effective_identity_async(session, mission_id=mission_id, criteria=criteria)
    current_link = await session.get(MissionMonitoringItem, mission_id, with_for_update=True)
    current_item_id = current_link.monitoring_item_id if current_link is not None else None

    target_item: MonitoringItem | None = None
    if identity is not None:
        target_item = await resolve_or_create_monitoring_item_async(session, identity, now=now)
    target_item_id = target_item.id if target_item is not None else None

    store_ids = (
        await session.scalars(
            select(MissionSource.store_id).where(MissionSource.mission_id == mission_id)
        )
    ).all()

    if target_item_id == current_item_id:
        if mission_is_active and current_item_id is not None:
            await _activate_item_stores_async(
                session, monitoring_item_id=current_item_id, store_ids=store_ids, now=now
            )
        return target_item

    if current_link is not None:
        await session.delete(current_link)
        await session.flush()
    if target_item is not None:
        session.add(
            MissionMonitoringItem(
                mission_id=mission_id, monitoring_item_id=target_item.id, created_at=now
            )
        )
        await session.flush()
        if mission_is_active:
            await _activate_item_stores_async(
                session, monitoring_item_id=target_item.id, store_ids=store_ids, now=now
            )
    if current_item_id is not None:
        await _deactivate_item_stores_if_unneeded_async(
            session,
            monitoring_item_id=current_item_id,
            store_ids=store_ids,
            excluding_mission_id=mission_id,
            now=now,
        )
    return target_item
