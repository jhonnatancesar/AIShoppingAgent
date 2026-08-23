"""Execução atômica do ciclo de vida persistente de missões.

`transition_mission`/`create_mission_from_criteria` têm uma versão síncrona
(mantida para `scripts/validate_collection_worker.py`) e uma versão
`_async` (extensão da TASK-079, usada pelo webhook Telegram) -- as duas
compartilham exatamente a mesma lógica de validação, só divergindo nos
pontos de I/O (`await`). `edit_mission_criteria` só tem chamador no webhook,
por isso foi convertida diretamente, sem versão síncrona."""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.collection.models import MissionOfferRelevance
from app.collection.relevance import OfferRelevance
from app.core.config import get_settings
from app.database.time import utc_now
from app.missions.models import (
    Mission,
    MissionCommand,
    MissionCriteria,
    MissionProductSelection,
    MissionSchedule,
    MissionSource,
    MissionStatus,
    MissionTransition,
    VariantSelectionMode,
)
from app.missions.schedule import staggered_next_run_at
from app.offers.models import Offer
from app.products.identity import ProductRequestKind, classify_product_request
from app.products.models import Product
from app.quotas.service import (
    check_mission_activation_quota,
    check_mission_activation_quota_async,
)
from app.stores.models import Store
from app.users.models import User

_DEFAULT_V1_SOURCE_CODES = (
    "pichau",
    "terabyte",
    "amazon",
    "kabum",
    "magalu",
    "mercadolivre",
)
_DEFAULT_SCHEDULE_INTERVAL_MINUTES = 60
_DEFAULT_SCHEDULE_STAGGER_SECONDS = 0
"""Fontes selecionáveis da V1 usadas quando o Intent não especifica nenhuma."""


class MissionCreationError(RuntimeError):
    """Falha interna inesperada ao criar uma missão (nunca causada pelo usuário)."""


class MissionTransitionError(RuntimeError):
    """Erro de domínio ao tentar mudar o estado de uma missão."""


class MissionVariantSelectionError(RuntimeError):
    """Escolha de variante incompatível com a missão ou com suas descobertas."""


def _apply_product_request_identity(criteria: MissionCriteria, text: str) -> None:
    """Mantém o contrato persistido sincronizado com o texto operacional."""
    request_identity = classify_product_request(text)
    criteria.request_kind = request_identity.kind.value
    criteria.requested_family_key = request_identity.family_key
    criteria.requested_identity_key = request_identity.identity_key
    criteria.requested_variant = request_identity.variant
    criteria.variant_selection_mode = (
        VariantSelectionMode.PENDING
        if request_identity.kind is ProductRequestKind.PRODUCT_FAMILY
        else VariantSelectionMode.NOT_REQUIRED
    )
    criteria.variant_prompted_at = None


async def set_mission_product_selection_async(
    session: AsyncSession,
    *,
    user_id: UUID,
    mission_id: UUID,
    expected_state_version: int,
    product_ids: Sequence[UUID] = (),
    select_all: bool = False,
    selected_at: datetime,
    allow_uncollected_family_products: bool = False,
) -> tuple[Product, ...]:
    """Persiste escolha explícita de variantes de uma `PRODUCT_FAMILY`."""
    mission = await session.scalar(
        select(Mission).where(Mission.id == mission_id).with_for_update()
    )
    if mission is None or mission.user_id != user_id:
        raise MissionNotFoundError("Missão não encontrada.")
    if mission.state_version != expected_state_version:
        raise MissionVersionConflictError("A missão mudou desde a última leitura.")
    criteria = await session.scalar(
        select(MissionCriteria).where(MissionCriteria.mission_id == mission_id)
    )
    if (
        criteria is None
        or criteria.request_kind != ProductRequestKind.PRODUCT_FAMILY.value
        or criteria.requested_family_key is None
    ):
        raise MissionVariantSelectionError(
            "Esta missão não exige seleção de variantes."
        )
    requested_ids = tuple(dict.fromkeys(product_ids))
    if select_all and requested_ids:
        raise MissionVariantSelectionError(
            "Escolha todas as variantes ou uma lista específica, não ambas."
        )
    if not select_all and not requested_ids:
        raise MissionVariantSelectionError("Escolha ao menos uma variante.")

    available_statement = select(Product).join(Offer, Offer.product_id == Product.id)
    if allow_uncollected_family_products:
        # TASK-099: seleção feita a partir da pesquisa read-only. O produto
        # precisa existir na família e em uma loja da missão; nenhuma
        # relevância é inventada antes da primeira coleta da missão.
        available_statement = available_statement.join(
            MissionSource, MissionSource.store_id == Offer.store_id
        ).where(MissionSource.mission_id == mission_id)
    else:
        available_statement = available_statement.join(
            MissionOfferRelevance,
            MissionOfferRelevance.offer_id == Offer.id,
        ).where(
            MissionOfferRelevance.mission_id == mission_id,
            MissionOfferRelevance.classification.in_(
                {OfferRelevance.MATCH, OfferRelevance.POSSIBLE_MATCH}
            ),
        )
    available_statement = (
        available_statement.where(
            Product.family_key == criteria.requested_family_key,
            Product.identity_key.is_not(None),
        )
        .distinct()
        .order_by(Product.display_name, Product.name, Product.id)
    )
    if criteria.requested_variant is not None:
        available_statement = available_statement.where(
            Product.variant == criteria.requested_variant
        )
    available = tuple(await session.scalars(available_statement))
    available_by_id = {product.id: product for product in available}
    if not available:
        raise MissionVariantSelectionError(
            "Ainda não há variantes identificadas com segurança para esta missão."
        )
    if not select_all and any(item not in available_by_id for item in requested_ids):
        raise MissionVariantSelectionError(
            "Uma das variantes não pertence às opções desta missão."
        )

    await session.execute(
        delete(MissionProductSelection).where(
            MissionProductSelection.mission_id == mission_id
        )
    )
    selected = available if select_all else tuple(available_by_id[item] for item in requested_ids)
    if not select_all:
        session.add_all(
            MissionProductSelection(
                mission_id=mission_id, product_id=product.id, created_at=selected_at
            )
            for product in selected
        )
    criteria.variant_selection_mode = (
        VariantSelectionMode.ALL if select_all else VariantSelectionMode.SELECTED
    )
    mission.state_version += 1
    mission.updated_at = selected_at
    mission.prelist_sent = False
    mission.prelist_errata_sent = False
    mission.prelist_lowest_amount = None
    mission.prelist_lowest_currency = None
    await session.flush()
    return selected


class MissionNotFoundError(MissionTransitionError):
    """A missão solicitada não existe."""


class MissionVersionConflictError(MissionTransitionError):
    """A missão mudou desde a versão conhecida pelo chamador."""


class InvalidMissionTransitionError(MissionTransitionError):
    """O comando não é permitido no estado persistido atual."""


class MissionTransitionConditionError(MissionTransitionError):
    """Uma condição obrigatória da transição não foi satisfeita."""


class MissionEditConditionError(MissionTransitionError):
    """Uma condição obrigatória da edição de critérios não foi satisfeita."""


TRANSITIONS: dict[tuple[MissionStatus, MissionCommand], MissionStatus] = {
    (MissionStatus.DRAFT, MissionCommand.ACTIVATE): MissionStatus.ACTIVE,
    (MissionStatus.DRAFT, MissionCommand.CANCEL): MissionStatus.CANCELLED,
    (MissionStatus.DRAFT, MissionCommand.EXPIRE): MissionStatus.EXPIRED,
    (MissionStatus.ACTIVE, MissionCommand.PAUSE): MissionStatus.PAUSED,
    (MissionStatus.ACTIVE, MissionCommand.COMPLETE): MissionStatus.COMPLETED,
    (MissionStatus.ACTIVE, MissionCommand.CANCEL): MissionStatus.CANCELLED,
    (MissionStatus.ACTIVE, MissionCommand.EXPIRE): MissionStatus.EXPIRED,
    (MissionStatus.PAUSED, MissionCommand.RESUME): MissionStatus.ACTIVE,
    (MissionStatus.PAUSED, MissionCommand.COMPLETE): MissionStatus.COMPLETED,
    (MissionStatus.PAUSED, MissionCommand.CANCEL): MissionStatus.CANCELLED,
    (MissionStatus.PAUSED, MissionCommand.EXPIRE): MissionStatus.EXPIRED,
}


def transition_mission(
    session: Session,
    *,
    mission_id: UUID,
    command: MissionCommand,
    expected_state_version: int,
    actor_type: str,
    actor_id: UUID | None = None,
    reason: str | None = None,
    transitioned_at: datetime | None = None,
) -> MissionTransition:
    """Valida, altera e registra uma transição na transação do chamador."""
    if not actor_type.strip() or len(actor_type) > 32:
        raise ValueError("actor_type deve conter entre 1 e 32 caracteres.")
    if reason is not None and not reason.strip():
        raise ValueError("reason não pode ser vazio.")
    if expected_state_version < 0:
        raise ValueError("expected_state_version não pode ser negativo.")

    accepted_at = transitioned_at or utc_now()
    if accepted_at.tzinfo is None or accepted_at.utcoffset() is None:
        raise ValueError("transitioned_at deve possuir fuso horário.")

    mission = session.scalar(
        select(Mission).where(Mission.id == mission_id).with_for_update()
    )
    if mission is None:
        raise MissionNotFoundError("Missão não encontrada.")
    if mission.state_version != expected_state_version:
        raise MissionVersionConflictError("Versão de estado desatualizada.")

    try:
        next_status = TRANSITIONS[(mission.status, command)]
    except KeyError as error:
        raise InvalidMissionTransitionError(
            f"Comando {command.value} inválido para o estado {mission.status.value}."
        ) from error

    if command in {MissionCommand.ACTIVATE, MissionCommand.RESUME}:
        criteria_id = session.scalar(
            select(MissionCriteria.id).where(MissionCriteria.mission_id == mission.id)
        )
        if criteria_id is None:
            raise MissionTransitionConditionError(
                "A missão precisa de critérios válidos para ser ativada."
            )
        source_id = session.scalar(
            select(MissionSource.store_id).where(MissionSource.mission_id == mission.id)
        )
        if source_id is None:
            raise MissionTransitionConditionError(
                "A missão precisa de ao menos uma fonte selecionada."
            )
        # TASK-107: cota checada aqui, antes de aplicar `next_status` -- a
        # missão ainda não é `ACTIVE` neste ponto, então a checagem não
        # conta a própria transição duas vezes. Nunca pausa/cancela nada
        # sozinho, só recusa a transição (`QuotaExceededError`).
        owner = session.scalar(select(User).where(User.id == mission.user_id))
        assert owner is not None  # FK RESTRICT garante que sempre existe
        check_mission_activation_quota(
            session,
            user=owner,
            mission_id=mission.id,
            settings=get_settings(),
            now=accepted_at,
        )
    if command is MissionCommand.RESUME and _deadline_reached(mission, accepted_at):
        raise MissionTransitionConditionError(
            "Uma missão expirada não pode ser retomada."
        )
    if command is MissionCommand.EXPIRE and not _deadline_reached(mission, accepted_at):
        raise MissionTransitionConditionError(
            "A missão só pode expirar depois de alcançar seu prazo."
        )

    previous_status = mission.status
    mission.status = next_status
    mission.state_version += 1
    mission.updated_at = accepted_at
    if command is MissionCommand.CANCEL:
        schedule = session.scalar(
            select(MissionSchedule)
            .where(MissionSchedule.mission_id == mission.id)
            .with_for_update()
        )
        if schedule is not None:
            schedule.is_enabled = False
            schedule.updated_at = accepted_at
    transition = MissionTransition(
        mission_id=mission.id,
        from_status=previous_status,
        to_status=next_status,
        command=command,
        actor_type=actor_type,
        actor_id=actor_id,
        reason=reason,
        transitioned_at=accepted_at,
    )
    session.add(transition)
    session.flush()
    return transition


async def transition_mission_async(
    session: AsyncSession,
    *,
    mission_id: UUID,
    command: MissionCommand,
    expected_state_version: int,
    actor_type: str,
    actor_id: UUID | None = None,
    reason: str | None = None,
    transitioned_at: datetime | None = None,
) -> MissionTransition:
    """Equivalente assíncrono de `transition_mission` (extensão da
    TASK-079). Usado pelo webhook Telegram; `scripts/validate_collection_worker.py`
    continua na versão síncrona."""
    if not actor_type.strip() or len(actor_type) > 32:
        raise ValueError("actor_type deve conter entre 1 e 32 caracteres.")
    if reason is not None and not reason.strip():
        raise ValueError("reason não pode ser vazio.")
    if expected_state_version < 0:
        raise ValueError("expected_state_version não pode ser negativo.")

    accepted_at = transitioned_at or utc_now()
    if accepted_at.tzinfo is None or accepted_at.utcoffset() is None:
        raise ValueError("transitioned_at deve possuir fuso horário.")

    mission = await session.scalar(
        select(Mission).where(Mission.id == mission_id).with_for_update()
    )
    if mission is None:
        raise MissionNotFoundError("Missão não encontrada.")
    if mission.state_version != expected_state_version:
        raise MissionVersionConflictError("Versão de estado desatualizada.")

    try:
        next_status = TRANSITIONS[(mission.status, command)]
    except KeyError as error:
        raise InvalidMissionTransitionError(
            f"Comando {command.value} inválido para o estado {mission.status.value}."
        ) from error

    if command in {MissionCommand.ACTIVATE, MissionCommand.RESUME}:
        criteria_id = await session.scalar(
            select(MissionCriteria.id).where(MissionCriteria.mission_id == mission.id)
        )
        if criteria_id is None:
            raise MissionTransitionConditionError(
                "A missão precisa de critérios válidos para ser ativada."
            )
        source_id = await session.scalar(
            select(MissionSource.store_id).where(MissionSource.mission_id == mission.id)
        )
        if source_id is None:
            raise MissionTransitionConditionError(
                "A missão precisa de ao menos uma fonte selecionada."
            )
        # TASK-107: mesma checagem de cota da versão síncrona.
        owner = await session.scalar(select(User).where(User.id == mission.user_id))
        assert owner is not None  # FK RESTRICT garante que sempre existe
        await check_mission_activation_quota_async(
            session,
            user=owner,
            mission_id=mission.id,
            settings=get_settings(),
            now=accepted_at,
        )
    if command is MissionCommand.RESUME and _deadline_reached(mission, accepted_at):
        raise MissionTransitionConditionError(
            "Uma missão expirada não pode ser retomada."
        )
    if command is MissionCommand.EXPIRE and not _deadline_reached(mission, accepted_at):
        raise MissionTransitionConditionError(
            "A missão só pode expirar depois de alcançar seu prazo."
        )

    previous_status = mission.status
    mission.status = next_status
    mission.state_version += 1
    mission.updated_at = accepted_at
    if command is MissionCommand.CANCEL:
        schedule = await session.scalar(
            select(MissionSchedule)
            .where(MissionSchedule.mission_id == mission.id)
            .with_for_update()
        )
        if schedule is not None:
            schedule.is_enabled = False
            schedule.updated_at = accepted_at
    transition = MissionTransition(
        mission_id=mission.id,
        from_status=previous_status,
        to_status=next_status,
        command=command,
        actor_type=actor_type,
        actor_id=actor_id,
        reason=reason,
        transitioned_at=accepted_at,
    )
    session.add(transition)
    await session.flush()
    return transition


def _deadline_reached(mission: Mission, accepted_at: datetime) -> bool:
    return mission.expires_at is not None and accepted_at >= mission.expires_at


def create_mission_from_criteria(
    session: Session,
    *,
    user_id: UUID,
    search_query: str,
    model: str | None = None,
    title: str | None = None,
    target_amount: Decimal | None,
    target_currency: str | None,
    source_codes: Sequence[str],
    requested_at: datetime,
    schedule_interval_minutes: int = _DEFAULT_SCHEDULE_INTERVAL_MINUTES,
    schedule_stagger_seconds: int = _DEFAULT_SCHEDULE_STAGGER_SECONDS,
    actor_type: str,
) -> tuple[Mission, tuple[str, ...]]:
    """Cria uma missão a partir de critérios e a ativa imediatamente.

    Quando `source_codes` vem vazio, usa automaticamente as quatro fontes
    selecionáveis da V1: toda missão criada por este serviço sai `active`,
    nunca `draft` por falta de fonte. Devolve a missão e as fontes
    efetivamente usadas, para que o chamador possa relatá-las ao usuário.

    `title` (TASK-083, correção de regressão) é opcional -- quando
    ausente, usa `search_query` como sempre. Existe para permitir um
    título de apresentação mais rico (`display_query` do
    `IntentInterpreter`) sem afetar `MissionCriteria.search_query`, que
    continua sendo a identidade operacional usada nas lojas.

    `actor_type` (TASK-092, auditoria de 2026-08-22, `DEC-075`) rotula a
    transição `draft` -> `active` automática desta função na auditoria
    (`MissionTransition.actor_type`) -- **obrigatório**, sem valor
    padrão, para bater com a convenção já usada por todo o resto do
    projeto (`transition_mission(_async)`, `app.privacy.service`,
    `scripts/validate_limits_resilience.py` -- nenhum desses tem
    `actor_type` opcional). Um valor implícito já mascarou uma vez a
    origem real de uma missão criada pela web como `"telegram"`; a
    correção certa não é trocar o valor padrão, é não ter um.
    """
    effective_codes = tuple(source_codes) or _DEFAULT_V1_SOURCE_CODES
    if schedule_interval_minutes <= 0:
        raise ValueError("schedule_interval_minutes deve ser positivo.")

    mission = Mission(
        id=uuid4(),
        user_id=user_id,
        title=(title or search_query)[:200],
        status=MissionStatus.DRAFT,
        state_version=0,
        created_at=requested_at,
        updated_at=requested_at,
    )
    session.add(mission)

    criteria = MissionCriteria(
        mission_id=mission.id,
        search_query=search_query,
        model=model,
        target_amount=target_amount,
        target_currency=target_currency,
        created_at=requested_at,
        updated_at=requested_at,
    )
    _apply_product_request_identity(
        criteria, f"{search_query} {model}" if model else search_query
    )
    session.add(criteria)

    stores_by_code = {
        store.code: store
        for store in session.scalars(
            select(Store).where(Store.code.in_(effective_codes))
        )
    }
    missing_codes = set(effective_codes) - stores_by_code.keys()
    if missing_codes:
        raise MissionCreationError(
            f"stores not seeded for codes: {', '.join(sorted(missing_codes))}"
        )

    for code in effective_codes:
        session.add(
            MissionSource(mission_id=mission.id, store_id=stores_by_code[code].id)
        )
    session.flush()

    transition_mission(
        session,
        mission_id=mission.id,
        command=MissionCommand.ACTIVATE,
        expected_state_version=mission.state_version,
        actor_type=actor_type,
        actor_id=user_id,
        transitioned_at=requested_at,
    )
    session.add(
        MissionSchedule(
            mission_id=mission.id,
            interval_minutes=schedule_interval_minutes,
            next_run_at=staggered_next_run_at(
                requested_at, max_stagger_seconds=schedule_stagger_seconds
            ),
            is_enabled=True,
            created_at=requested_at,
            updated_at=requested_at,
        )
    )
    session.flush()
    return mission, effective_codes


async def create_mission_from_criteria_async(
    session: AsyncSession,
    *,
    user_id: UUID,
    search_query: str,
    model: str | None = None,
    title: str | None = None,
    target_amount: Decimal | None,
    target_currency: str | None,
    source_codes: Sequence[str],
    requested_at: datetime,
    schedule_interval_minutes: int = _DEFAULT_SCHEDULE_INTERVAL_MINUTES,
    schedule_stagger_seconds: int = _DEFAULT_SCHEDULE_STAGGER_SECONDS,
    actor_type: str,
) -> tuple[Mission, tuple[str, ...]]:
    """Equivalente assíncrono de `create_mission_from_criteria` (extensão
    da TASK-079). Usado pelo webhook Telegram (`actor_type="telegram"`) e,
    desde a TASK-092, pelo endpoint web `POST /api/v1/missions`
    (`actor_type="web"`); `scripts/validate_collection_worker.py` continua
    na versão síncrona. `title`/`actor_type` -- ver docstring da versão
    síncrona (TASK-083, TASK-092)."""
    effective_codes = tuple(source_codes) or _DEFAULT_V1_SOURCE_CODES
    if schedule_interval_minutes <= 0:
        raise ValueError("schedule_interval_minutes deve ser positivo.")

    mission = Mission(
        id=uuid4(),
        user_id=user_id,
        title=(title or search_query)[:200],
        status=MissionStatus.DRAFT,
        state_version=0,
        created_at=requested_at,
        updated_at=requested_at,
    )
    session.add(mission)

    criteria = MissionCriteria(
        mission_id=mission.id,
        search_query=search_query,
        model=model,
        target_amount=target_amount,
        target_currency=target_currency,
        created_at=requested_at,
        updated_at=requested_at,
    )
    _apply_product_request_identity(
        criteria, f"{search_query} {model}" if model else search_query
    )
    session.add(criteria)

    stores_by_code = {
        store.code: store
        for store in await session.scalars(
            select(Store).where(Store.code.in_(effective_codes))
        )
    }
    missing_codes = set(effective_codes) - stores_by_code.keys()
    if missing_codes:
        raise MissionCreationError(
            f"stores not seeded for codes: {', '.join(sorted(missing_codes))}"
        )

    for code in effective_codes:
        session.add(
            MissionSource(mission_id=mission.id, store_id=stores_by_code[code].id)
        )
    await session.flush()

    await transition_mission_async(
        session,
        mission_id=mission.id,
        command=MissionCommand.ACTIVATE,
        expected_state_version=mission.state_version,
        actor_type=actor_type,
        actor_id=user_id,
        transitioned_at=requested_at,
    )
    session.add(
        MissionSchedule(
            mission_id=mission.id,
            interval_minutes=schedule_interval_minutes,
            next_run_at=staggered_next_run_at(
                requested_at, max_stagger_seconds=schedule_stagger_seconds
            ),
            is_enabled=True,
            created_at=requested_at,
            updated_at=requested_at,
        )
    )
    await session.flush()
    return mission, effective_codes


async def edit_mission_criteria(
    session: AsyncSession,
    *,
    mission_id: UUID,
    expected_state_version: int,
    target_update: tuple[Decimal | None, str | None] | None,
    source_codes: Sequence[str] | None,
    edited_at: datetime | None = None,
) -> tuple[Mission, tuple[str, ...]]:
    """Edita preço-alvo e/ou lojas de uma missão `PAUSED` já criada (TASK-069).

    Nunca cria uma `Mission` nova nem toca `status`/`MissionTransition`/
    `MissionSchedule` -- edição de critérios é conceitualmente distinta de
    transição de ciclo de vida. Histórico já coletado (`CollectionRun`/
    `PriceObservation`) nunca é tocado, mesmo para lojas removidas.

    **`state_version` (correção da auditoria de TASK-092/DEC-075):**
    representa a versão concorrente da missão inteira, não só do
    lifecycle -- toda edição bem-sucedida incrementa `state_version`, do
    mesmo jeito que toda transição de `transition_mission(_async)`. Sem
    isso, dois `expected_state_version` iguais e concorrentes passariam
    ambos pela checagem abaixo e a segunda escrita apagaria a primeira em
    silêncio (last-write-wins) sempre que tocassem o mesmo campo -- exatamente
    o que a checagem de `expected_state_version` deveria impedir. Manter
    a checagem sem o incremento correspondente era uma proteção
    incompleta, não uma escolha de desenho.

    `target_update=None` deixa o preço-alvo intocado; um par
    `(amount, currency)` -- incluindo `(None, None)` para limpar o alvo --
    sobrescreve. `source_codes=None` deixa as lojas intocadas; uma
    sequência não vazia substitui inteiramente o conjunto de
    `MissionSource` (insere as novas, remove as que saíram). Pelo menos um
    dos dois precisa ser fornecido; devolve a missão e o conjunto de
    lojas efetivamente selecionado após a edição.
    """
    if target_update is None and source_codes is None:
        raise ValueError("target_update or source_codes must be provided")
    if target_update is not None and (target_update[0] is None) != (
        target_update[1] is None
    ):
        raise ValueError("target_update amount and currency must be paired")
    if source_codes is not None and not source_codes:
        raise MissionEditConditionError(
            "A missão precisa manter ao menos uma loja selecionada."
        )
    if expected_state_version < 0:
        raise ValueError("expected_state_version não pode ser negativo.")

    accepted_at = edited_at or utc_now()
    if accepted_at.tzinfo is None or accepted_at.utcoffset() is None:
        raise ValueError("edited_at deve possuir fuso horário.")

    mission = await session.scalar(
        select(Mission).where(Mission.id == mission_id).with_for_update()
    )
    if mission is None:
        raise MissionNotFoundError("Missão não encontrada.")
    if mission.state_version != expected_state_version:
        raise MissionVersionConflictError("Versão de estado desatualizada.")
    if mission.status is not MissionStatus.PAUSED:
        raise MissionEditConditionError(
            "Só é possível editar uma missão pausada. Pause a missão primeiro."
        )

    if target_update is not None:
        criteria = await session.scalar(
            select(MissionCriteria).where(MissionCriteria.mission_id == mission.id)
        )
        if criteria is None:
            raise MissionEditConditionError("A missão não tem critérios válidos.")
        criteria.target_amount = target_update[0]
        criteria.target_currency = target_update[1]
        criteria.updated_at = accepted_at

    effective_codes: tuple[str, ...] = ()
    if source_codes is not None:
        codes = tuple(dict.fromkeys(source_codes))
        stores_by_code = {
            store.code: store
            for store in await session.scalars(
                select(Store).where(Store.code.in_(codes))
            )
        }
        missing_codes = set(codes) - stores_by_code.keys()
        if missing_codes:
            raise MissionEditConditionError(
                f"Loja(s) não reconhecida(s): {', '.join(sorted(missing_codes))}."
            )
        current_store_ids = set(
            await session.scalars(
                select(MissionSource.store_id).where(
                    MissionSource.mission_id == mission.id
                )
            )
        )
        target_store_ids = {stores_by_code[code].id for code in codes}
        removed_store_ids = current_store_ids - target_store_ids
        if removed_store_ids:
            await session.execute(
                delete(MissionSource).where(
                    MissionSource.mission_id == mission.id,
                    MissionSource.store_id.in_(removed_store_ids),
                )
            )
        for store_id in target_store_ids - current_store_ids:
            session.add(MissionSource(mission_id=mission.id, store_id=store_id))
        await session.flush()
        effective_codes = codes

    mission.state_version += 1
    mission.updated_at = accepted_at
    await session.flush()
    return mission, effective_codes


async def promote_confirmed_product_identity_async(
    session: AsyncSession,
    *,
    mission_id: UUID,
    confirmed_search_query: str,
    promoted_at: datetime | None = None,
) -> bool:
    """Promove uma identidade confirmada por `ProductIdentityResolver`
    (Kabum/Amazon, `collection_worker`) à missão (TASK-083, correção de
    regressão).

    Chamado só depois de uma correspondência real de título contra uma
    loja -- nunca por invenção/enriquecimento da IA. Atualiza
    `MissionCriteria.search_query` (identidade operacional -- passa a ser
    o texto confirmado, o que também faz `_needs_identity_resolution` não
    disparar de novo em nenhum próximo batch, já que deixa de bater no
    padrão "só o código cru") e `Mission.title` (apresentação). Nunca
    altera `MissionCriteria.model`: o código cru continua correto e é o
    que o matcher determinístico usa contra candidatos reais em toda
    coleta futura -- nunca foi a origem do problema.

    Não faz nenhuma chamada externa (Playwright/IA) -- só leitura/escrita
    local, chamado pelo `collection_worker` inteiramente fora da janela
    de tempo em que a resolução externa aconteceu. `mission_id` ausente
    (missão apagada/inexistente) é tratado como no-op silencioso: a
    resolução em memória já serviu a coleta deste batch, persistir a
    identidade é só um enriquecimento, nunca um requisito.
    """
    if not confirmed_search_query.strip():
        raise ValueError("confirmed_search_query não pode ser vazio.")
    accepted_at = promoted_at or utc_now()

    mission = await session.scalar(
        select(Mission).where(Mission.id == mission_id).with_for_update()
    )
    if mission is None:
        return False
    criteria = await session.scalar(
        select(MissionCriteria).where(MissionCriteria.mission_id == mission_id)
    )
    if criteria is None:
        return False

    criteria.search_query = confirmed_search_query
    _apply_product_request_identity(
        criteria,
        (
            f"{confirmed_search_query} {criteria.model}"
            if criteria.model
            else confirmed_search_query
        ),
    )
    await session.execute(
        delete(MissionProductSelection).where(
            MissionProductSelection.mission_id == mission_id
        )
    )
    criteria.updated_at = accepted_at
    mission.title = confirmed_search_query[:200]
    mission.state_version += 1
    mission.updated_at = accepted_at
    mission.prelist_sent = False
    mission.prelist_errata_sent = False
    mission.prelist_lowest_amount = None
    mission.prelist_lowest_currency = None
    await session.flush()
    return True
