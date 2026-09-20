"""Orquestração com banco do aprendizado de identidade assistido por IA
(rodada de 2026-09-12) -- camada que liga o motor determinístico
(`app.products.identity`), a extração por IA (`app.products.
identity_ai`) e a persistência (`app.products.identity_candidates`).

`resolve_or_learn_product_variant` é chamada pela Fase B do
`CollectionOrchestrator` (`app.collection.orchestration._classify`,
atrás da flag `product_identity_learning_enabled`, default `False`) --
mesma disciplina de sessão curta/sem transação aberta durante a
chamada de IA (I/O de rede). `apply_learned_identity` é aplicada na
Fase C, dentro da seção crítica por `mission_id`; como essa seção NÃO
é serializada entre missões diferentes (só por `mission_id`), duas
missões concorrentes podem legitimamente aprender/reaproveitar o MESMO
`identity_key` ao mesmo tempo -- `apply_learned_identity` protege essa
corrida com `pg_advisory_xact_lock` por `identity_key` antes de
decidir promover ou migrar."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_provider import AIProviderManager
from app.database.time import utc_now
from app.offers.models import Offer
from app.products.identity import (
    IDENTITY_VERSION,
    ResolvedProductVariant,
    _slug,
    normalize_for_grounding,
    resolve_product_variant,
)
from app.products.identity_ai import (
    AIIdentityExtraction,
    evaluate_ai_identity_extraction,
    extract_product_identities_via_ai_batch,
    extract_product_identity_via_ai,
    normalized_title_hash,
    tokens_present,
    values_match_ignoring_punctuation,
)
from app.products.identity_arbiter import (
    ArbiterVerdict,
    ListingEvidence,
    arbitrate_same_product,
)
from app.products.identity_candidates import ProductIdentityCandidate
from app.products.models import Product
from app.users.models import UserRole

logger = logging.getLogger("app.products.identity_learning")


async def _find_candidate(
    session: AsyncSession, title_hash: str
) -> ProductIdentityCandidate | None:
    return await session.scalar(
        select(ProductIdentityCandidate).where(
            ProductIdentityCandidate.normalized_title_hash == title_hash
        )
    )


def _resolved_from_candidate(
    candidate: ProductIdentityCandidate | _ArbitrationCandidate,
) -> ResolvedProductVariant:
    """Um candidato `approved` já tem todos os campos validados no
    momento em que foi aprovado -- reconstrói o `ResolvedProductVariant`
    diretamente das colunas persistidas, sem rodar grounding de novo
    (grounding é uma checagem de ACEITAÇÃO, feita uma vez; não muda
    reconsultando a mesma linha)."""
    return ResolvedProductVariant(
        category=candidate.category,
        brand=candidate.brand,
        family=candidate.family,
        model=candidate.model,
        variant=candidate.variant,
        attributes=tuple(sorted(candidate.attributes.items())),
        family_key=candidate.family_key,
        identity_key=candidate.identity_key,
        label=f"{candidate.brand.title()} {candidate.family.title()} {candidate.model.upper()}".strip(),
    )


def _has_unrecognized_model_suffix(
    raw_title_normalized: str, model: str, known_tokens: frozenset[str]
) -> bool:
    """Achado real (rodada de 2026-09-12, ao testar dois títulos "27GP850"
    vs "27GP850-B"): comparar por CONJUNTO de tokens reconhece "27GP850"
    como presente dentro de "27GP850-B" (o hífen vira espaço na
    normalização, separando em dois tokens ["27GP850", "B"]) -- mas
    "-B" pode ser um sufixo de REVISÃO/VARIANTE real (mesmo padrão de
    "B650"/"B650E", só que aqui separado por hífen em vez de colado).
    Um candidato aprovado só para "27GP850" nunca deveria reaproveitar
    silenciosamente para um título que on menciona um sufixo curto
    (1-2 caracteres alfanuméricos) logo depois do modelo que o
    candidato NÃO tem registrado em `variant`/`attributes`.

    Fail-closed deliberado: quando este sufixo aparece, o reconhecimento
    por tokens RECUSA o candidato (força uma nova extração via IA) --
    melhor gastar uma chamada extra de IA do que unificar duas variantes
    de hardware diferentes."""
    title_tokens = raw_title_normalized.split()
    model_tokens = normalize_for_grounding(model).split()
    span = len(model_tokens)
    if span == 0:
        return False
    for start in range(len(title_tokens) - span + 1):
        if title_tokens[start : start + span] != model_tokens:
            continue
        next_index = start + span
        if next_index >= len(title_tokens):
            continue
        next_token = title_tokens[next_index]
        if (
            1 <= len(next_token) <= 2
            and next_token.isalnum()
            and next_token not in known_tokens
        ):
            return True
    return False


async def _find_reusable_candidate_by_tokens(
    session: AsyncSession, raw_title_normalized: str
) -> ProductIdentityCandidate | None:
    """Reconhece um título DIFERENTE (outra loja, outro wording) como a
    MESMA família/modelo/variante já aprendida -- sem cache de hash
    exato, que só reaproveita o MESMO texto (achado do dono do produto:
    "não trate cache do mesmo título como prova completa de aprendizado
    reutilizável"). Um candidato `approved` só é reaproveitado quando
    TODOS os seus tokens de marca/família/modelo E o valor de CADA
    atributo (`variant` incluso) aparecem literalmente no NOVO título --
    mesmo critério de grounding usado para aceitar uma extração de IA
    (`identity_ai._is_grounded`), aplicado agora contra conhecimento já
    aprovado. Um atributo AUSENTE ou DIFERENTE no novo título nunca
    reaproveita -- é tratado como possível variante distinta (nova
    extração via IA), nunca uma correspondência aproximada por marca/
    família/modelo sozinhos (a mesma lacuna que motivou reforçar
    `_is_grounded` para incluir atributos: "B650" vs "B650E" ou "DDR4"
    vs "DDR5" nunca podem colapsar no mesmo `identity_key` só porque a
    maioria dos tokens do título coincide).

    Custo: varre todos os candidatos `approved` (aceitável na escala
    atual de poucas categorias/famílias aprendidas; se o volume crescer
    muito, precisa de um índice melhor que varredura linear -- não
    implementado aqui por não haver ainda evidência real desse volume)."""
    candidates = await session.scalars(
        select(ProductIdentityCandidate).where(
            ProductIdentityCandidate.status == "approved"
        )
    )
    for candidate in candidates:
        if not all(
            tokens_present(raw_title_normalized, field)
            for field in (candidate.brand, candidate.family, candidate.model)
        ):
            continue
        if candidate.variant != "base" and not tokens_present(
            raw_title_normalized, candidate.variant
        ):
            continue
        if not all(
            tokens_present(raw_title_normalized, value)
            for value in candidate.attributes.values()
        ):
            continue
        known_tokens = frozenset(
            normalize_for_grounding(candidate.variant).split()
        ) | frozenset(
            token
            for value in candidate.attributes.values()
            for token in normalize_for_grounding(value).split()
        )
        if _has_unrecognized_model_suffix(
            raw_title_normalized, candidate.model, known_tokens
        ):
            continue
        return candidate
    return None


def _listing_evidence_from_candidate(
    candidate: _ArbitrationCandidate,
) -> ListingEvidence:
    return ListingEvidence(
        manufacturer=candidate.brand,
        family=candidate.family,
        model_name=candidate.model,
        variant=None if candidate.variant == "base" else candidate.variant,
        store_sku=candidate.store_sku,
        manufacturer_part_number=candidate.manufacturer_part_number,
        attributes=candidate.attributes,
    )


def _listing_evidence_from_extraction(
    extraction: AIIdentityExtraction,
) -> ListingEvidence:
    return ListingEvidence(
        manufacturer=extraction.brand,
        family=extraction.family,
        model_name=extraction.model,
        variant=extraction.variant,
        store_sku=extraction.store_sku,
        manufacturer_part_number=extraction.manufacturer_part_number,
        attributes=extraction.attributes,
    )


@dataclass(frozen=True, slots=True)
class _ArbitrationCandidate:
    """Cópia congelada dos campos de um `ProductIdentityCandidate` da
    zona cinzenta, feita ENQUANTO a instância ORM ainda está fresca
    (dentro de `_find_same_model_candidates`, antes de qualquer
    `await session.rollback()`).

    Achado real (checkpoint 3, 2026-09-13): `resolve_or_learn_product_
    variant` chama `session.rollback()` entre buscar estes candidatos e
    usá-los (para não segurar transação aberta durante a chamada de
    rede à IA) -- `rollback()` expira TODAS as instâncias da sessão,
    então ler um atributo do `ProductIdentityCandidate` original depois
    disso tenta um lazy-load síncrono fora do greenlet async e explode
    com `sqlalchemy.exc.MissingGreenlet`. Este snapshot elimina o
    problema na raiz: nenhum atributo é lido do ORM depois do
    rollback, só deste dataclass."""

    category: str
    brand: str
    family: str
    model: str
    variant: str
    attributes: dict[str, str]
    store_sku: str | None
    manufacturer_part_number: str | None
    family_key: str
    identity_key: str


class DimensionVerdict(StrEnum):
    """Classificação semântica de UMA dimensão de evidência (`variant`,
    um atributo específico ou `manufacturer_part_number`) entre o que já
    foi aprendido para um candidato `approved` e o que a extração da IA
    observou no NOVO título (redesenho do matcher determinístico, seção
    3 do pedido de 2026-09-14): o objetivo é o matcher decidir sozinho
    quando for seguro, sem gastar chamada de árbitro só para compensar
    ausência de informação de um lado."""

    MATCH = "MATCH"
    MISSING = "MISSING"
    CONTRADICTORY = "CONTRADICTORY"


def _classify_dimension(known: str | None, observed: str | None) -> DimensionVerdict:
    """Ausência de qualquer lado NUNCA é contradição -- só `MISSING`. Só
    vira `CONTRADICTORY` quando os dois lados têm um valor EXPLÍCITO e
    incompatível, com a MESMA tolerância de pontuação/separador usada em
    todo o resto do módulo (`values_match_ignoring_punctuation`)."""
    if not known or not observed:
        return DimensionVerdict.MISSING
    if values_match_ignoring_punctuation(known, observed):
        return DimensionVerdict.MATCH
    return DimensionVerdict.CONTRADICTORY


def _classify_candidate_against_extraction(
    candidate: _ArbitrationCandidate, extraction: AIIdentityExtraction
) -> dict[str, DimensionVerdict]:
    """Classifica cada dimensão de evidência comparável entre um
    candidato já aprovado e a extração do NOVO título. Deliberadamente
    NUNCA inclui `store_sku`: é identificador da LOJA, nunca do produto
    -- divergir entre lojas nunca é evidência de produto diferente
    (seção 5 do pedido). Só entra no dicionário uma dimensão em que
    pelo menos um dos dois lados tem valor -- quando os dois lados nunca
    mencionaram aquele atributo, não há evidência nenhuma a classificar
    (nem MATCH, nem MISSING)."""
    verdicts: dict[str, DimensionVerdict] = {}

    known_variant = None if candidate.variant == "base" else candidate.variant
    if known_variant or extraction.variant:
        verdicts["variant"] = _classify_dimension(known_variant, extraction.variant)

    # `candidate.attributes` já foi persistido com o nome do atributo em
    # slug (`build_resolved_variant_from_fields`, ex.: "memory-type"),
    # mas `extraction.attributes` traz o nome de campo BRUTO devolvido
    # pela IA (ex.: "memory_type") -- comparar as chaves sem normalizar
    # os dois lados pela MESMA regra faz "memory-type" e "memory_type"
    # virarem duas dimensões diferentes (cada uma com só um lado
    # preenchido), mascarando uma contradição real como dois MISSING.
    known_attributes = {
        _slug(key): value for key, value in candidate.attributes.items()
    }
    observed_attributes = {
        _slug(key): value for key, value in extraction.attributes.items()
    }
    for key in sorted(set(known_attributes) | set(observed_attributes)):
        known_value = known_attributes.get(key)
        observed_value = observed_attributes.get(key)
        if known_value or observed_value:
            verdicts[f"attribute:{key}"] = _classify_dimension(
                known_value, observed_value
            )

    if candidate.manufacturer_part_number or extraction.manufacturer_part_number:
        verdicts["manufacturer_part_number"] = _classify_dimension(
            candidate.manufacturer_part_number, extraction.manufacturer_part_number
        )

    return verdicts


async def _find_same_model_candidates(
    session: AsyncSession, raw_title_normalized: str
) -> list[_ArbitrationCandidate]:
    """Candidatos aprovados cuja marca/família/modelo (tokens) já
    aparecem no NOVO título mas que `_find_reusable_candidate_by_tokens`
    recusou por causa de `variant`/atributo ausente ou divergente --
    zona cinzenta (checkpoint 3, 2026-09-13): um atributo/SKU ausente
    numa das lojas NUNCA prova produto diferente por si só, então esses
    casos vão para `app.products.identity_arbiter` em vez de virarem
    automaticamente uma extração isolada (que geraria um `identity_key`
    novo e nunca se fundiria com o já aprovado)."""
    candidates = await session.scalars(
        select(ProductIdentityCandidate).where(
            ProductIdentityCandidate.status == "approved"
        )
    )
    return [
        _ArbitrationCandidate(
            category=candidate.category,
            brand=candidate.brand,
            family=candidate.family,
            model=candidate.model,
            variant=candidate.variant,
            attributes=dict(candidate.attributes),
            store_sku=candidate.store_sku,
            manufacturer_part_number=candidate.manufacturer_part_number,
            family_key=candidate.family_key,
            identity_key=candidate.identity_key,
        )
        for candidate in candidates
        if all(
            tokens_present(raw_title_normalized, field)
            for field in (candidate.brand, candidate.family, candidate.model)
        )
    ]


@dataclass(frozen=True, slots=True)
class _PreparedResolution:
    """Resultado da fase SEM chamada de extração por IA (motor
    determinístico, cache de hash exato, reuso por tokens e o
    levantamento -- só leitura -- de candidatos da zona cinzenta).

    `done=True` já é a resolução final (`resolved` pode ser `None`
    quando o hash exato já tinha uma decisão não-aprovada -- mesmo
    fail-closed de sempre). `done=False` significa que só uma
    extração por IA pode resolver este título; `title_hash`/
    `arbitration_candidates` seguem para `_finish_resolution_with_
    extraction` depois que essa extração acontecer (single ou em
    lote)."""

    done: bool
    resolved: ResolvedProductVariant | None
    title_hash: str | None = None
    arbitration_candidates: tuple[_ArbitrationCandidate, ...] = ()


async def _prepare_resolution(
    session: AsyncSession,
    raw_title: str,
    *,
    now: datetime | None = None,
) -> _PreparedResolution:
    """Tenta resolver SEM nenhuma chamada de extração por IA -- motor
    determinístico, cache de hash exato, reuso por tokens contra
    conhecimento já aprovado (ver `_find_reusable_candidate_by_tokens`).
    Extraída de `resolve_or_learn_product_variant` para ser reaproveitada
    também pelo caminho em lote (`reprocess_unresolved_products`,
    `batch_size > 1`) -- mesma ordem/regras, nenhuma mudança de
    comportamento para o chamador de item único."""
    deterministic = resolve_product_variant(raw_title)
    if deterministic is not None:
        return _PreparedResolution(done=True, resolved=deterministic)

    title_hash = normalized_title_hash(raw_title)
    existing = await _find_candidate(session, title_hash)
    if existing is not None:
        if existing.status != "approved":
            return _PreparedResolution(done=True, resolved=None)
        return _PreparedResolution(
            done=True, resolved=_resolved_from_candidate(existing)
        )

    # Reconhecimento por tokens contra conhecimento JÁ APROVADO -- título
    # DIFERENTE (loja/wording diferente) da MESMA família/modelo/variante,
    # sem nova chamada de IA. Isto é o que prova aprendizado REUTILIZÁVEL
    # de verdade (não só cache do mesmo texto exato, ver docstring de
    # `_find_reusable_candidate_by_tokens`).
    raw_title_normalized = normalize_for_grounding(raw_title)
    reusable = await _find_reusable_candidate_by_tokens(session, raw_title_normalized)
    if reusable is not None:
        alias = ProductIdentityCandidate(
            id=uuid4(),
            raw_title=raw_title[:2000],
            normalized_title_hash=title_hash,
            category=reusable.category,
            brand=reusable.brand,
            family=reusable.family,
            model=reusable.model,
            variant=reusable.variant,
            attributes=dict(reusable.attributes),
            store_sku=reusable.store_sku,
            manufacturer_part_number=reusable.manufacturer_part_number,
            family_key=reusable.family_key,
            identity_key=reusable.identity_key,
            status="approved",
            grounded=True,
            ai_provider=reusable.ai_provider,
            ai_model=reusable.ai_model,
            created_at=now or utc_now(),
        )
        try:
            async with session.begin_nested():
                session.add(alias)
                await session.flush()
        except IntegrityError:
            # Corrida: outra coleta concorrente já persistiu este MESMO
            # hash entre a consulta e este INSERT -- o conteúdo (mesma
            # família/modelo já reconhecida) é equivalente; só evita
            # duplicar a linha, nunca perde a resolução.
            pass
        return _PreparedResolution(
            done=True, resolved=_resolved_from_candidate(reusable)
        )

    # Candidatos de mesma marca/família/modelo que não venceram o reuso
    # acima só por causa de variant/atributo ausente ou divergente --
    # decididos pelo árbitro de IA em `_finish_resolution_with_extraction`
    # (zona cinzenta), nunca tratados como produto diferente
    # automaticamente.
    arbitration_candidates = await _find_same_model_candidates(
        session, raw_title_normalized
    )
    return _PreparedResolution(
        done=False,
        resolved=None,
        title_hash=title_hash,
        arbitration_candidates=tuple(arbitration_candidates),
    )


async def _finish_resolution_with_extraction(
    session: AsyncSession,
    *,
    raw_title: str,
    title_hash: str,
    arbitration_candidates: tuple[_ArbitrationCandidate, ...],
    extraction: AIIdentityExtraction,
    ai_manager: AIProviderManager,
    arbiter_ai_manager: AIProviderManager | None,
    now: datetime | None = None,
) -> ResolvedProductVariant | None:
    """Continuação de `_prepare_resolution` depois que uma extração de
    IA (item único ou uma posição de um lote) já existe -- zona
    cinzenta (árbitro) + avaliação de grounding + persistência do
    candidato. Extraída de `resolve_or_learn_product_variant` sem
    nenhuma mudança de comportamento; `arbiter_ai_manager` segue a
    MESMA regra documentada lá (separado do `ai_manager` principal,
    nunca reaproveitado -- bug real do checkpoint 3)."""
    if arbitration_candidates:
        listing_b = _listing_evidence_from_extraction(extraction)
        for candidate in arbitration_candidates:
            # Matching determinístico primeiro (checkpoint 3, seção 6):
            # o MESMO `manufacturer_part_number` em duas lojas é
            # evidência forte o bastante para fundir sem gastar uma
            # chamada de IA no árbitro -- mesmo quando outro atributo
            # relatado diverge (pode ser erro de cadastro da própria
            # loja, não do fabricante).
            if values_match_ignoring_punctuation(
                candidate.manufacturer_part_number,
                extraction.manufacturer_part_number,
            ):
                verdict = ArbiterVerdict.SAME_PRODUCT
            else:
                # Redesenho MATCH/MISSING/CONTRADICTORY (seção 3 do
                # pedido de 2026-09-14): o matcher determinístico só
                # recorre ao árbitro de IA quando existe uma CONTRADIÇÃO
                # real em alguma dimensão -- uma dimensão apenas AUSENTE
                # de um lado (MISSING) nunca, sozinha, impede o
                # reaproveitamento determinístico do candidato já
                # aprovado (a zona cinzenta deixa de ser usada para
                # compensar semântica incorreta do matcher).
                dimension_verdicts = _classify_candidate_against_extraction(
                    candidate, extraction
                )
                has_contradiction = any(
                    verdict_value is DimensionVerdict.CONTRADICTORY
                    for verdict_value in dimension_verdicts.values()
                )
                if has_contradiction:
                    verdict = await arbitrate_same_product(
                        arbiter_ai_manager or ai_manager,
                        listing_a=_listing_evidence_from_candidate(candidate),
                        listing_b=listing_b,
                    )
                else:
                    verdict = ArbiterVerdict.SAME_PRODUCT
            if verdict is not ArbiterVerdict.SAME_PRODUCT:
                continue
            alias = ProductIdentityCandidate(
                id=uuid4(),
                raw_title=raw_title[:2000],
                normalized_title_hash=title_hash,
                category=candidate.category,
                brand=candidate.brand,
                family=candidate.family,
                model=candidate.model,
                variant=candidate.variant,
                attributes=dict(candidate.attributes),
                store_sku=extraction.store_sku,
                manufacturer_part_number=extraction.manufacturer_part_number,
                family_key=candidate.family_key,
                identity_key=candidate.identity_key,
                status="approved",
                grounded=True,
                ai_provider=extraction.ai_provider,
                ai_model=extraction.ai_model,
                reviewer_note="arbitered_same_product",
                created_at=now or utc_now(),
            )
            try:
                async with session.begin_nested():
                    session.add(alias)
                    await session.flush()
            except IntegrityError:
                pass
            return _resolved_from_candidate(candidate)

    evaluated = evaluate_ai_identity_extraction(raw_title, extraction)
    if evaluated.resolved is None:
        # Caso degenerado (ex.: campos só com símbolos, slug vazio) --
        # não há como calcular family_key/identity_key para persistir
        # como candidato; registra e desiste, nunca inventa.
        logger.warning(
            "product_identity_ai_extraction_unresolvable",
            extra={"raw_title": raw_title[:200]},
        )
        return None

    candidate = ProductIdentityCandidate(
        id=uuid4(),
        raw_title=raw_title[:2000],
        normalized_title_hash=title_hash,
        category=evaluated.resolved.category,
        brand=evaluated.resolved.brand,
        family=evaluated.resolved.family,
        model=evaluated.resolved.model,
        variant=evaluated.resolved.variant,
        attributes=dict(evaluated.resolved.attributes),
        store_sku=evaluated.raw.store_sku,
        manufacturer_part_number=evaluated.raw.manufacturer_part_number,
        family_key=evaluated.resolved.family_key,
        identity_key=evaluated.resolved.identity_key,
        status=evaluated.status,
        grounded=evaluated.grounded,
        ai_provider=evaluated.raw.ai_provider,
        ai_model=evaluated.raw.ai_model,
        created_at=now or utc_now(),
    )
    try:
        async with session.begin_nested():
            session.add(candidate)
            await session.flush()
    except IntegrityError:
        # Corrida real: outra coleta concorrente já aprendeu o MESMO
        # título entre a consulta acima e este INSERT -- nunca duplica
        # nem propaga o erro; relê a decisão que já venceu.
        winner = await _find_candidate(session, title_hash)
        if winner is None or winner.status != "approved":
            return None
        return _resolved_from_candidate(winner)

    if evaluated.status != "approved":
        return None
    return evaluated.resolved


async def resolve_or_learn_product_variant(
    session: AsyncSession,
    *,
    raw_title: str,
    ai_manager: AIProviderManager,
    profile: UserRole = UserRole.ADMIN,
    arbiter_ai_manager: AIProviderManager | None = None,
    now: datetime | None = None,
) -> ResolvedProductVariant | None:
    """Resolve a identidade de um título, aprendendo uma proposta nova
    via IA quando necessário -- SEMPRE tenta o motor determinístico
    primeiro (`resolve_product_variant`, os 5 extratores regex
    existentes NUNCA são substituídos ou contornados). Só recorre à IA
    quando o motor determinístico não reconhece o título.

    Contrato de concorrência: assume que `session` NÃO está no meio de
    uma transação que precisa permanecer curta -- esta função faz I/O
    de rede (chamada de IA) quando não há candidato já decidido para
    este título. Chame a partir de uma fase sem seção crítica de banco
    aberta (mesmo espírito de Fase B do `CollectionOrchestrator`,
    TASK-079) -- nunca de dentro de `_resolve_offer`/`_persist_phase_a`.

    Devolve `None` quando: o motor determinístico falhou E não há
    candidato aprovado E (a extração por IA falhou OU não passou no
    grounding determinístico) -- nesse último caso, uma proposta fica
    registrada em `product_identity_candidates` com `status=
    "pending_review"` para revisão humana, mas a chamada atual segue
    tratando o produto como não identificado (fail-closed, mesmo
    comportamento de sempre para título não reconhecido).

    `arbiter_ai_manager` -- achado real confirmado empiricamente
    (checkpoint 3, 2026-09-13): `app.products.identity_arbiter.
    arbitrate_same_product` SEMPRE envia `profile=UserRole.USER`
    (é assim que garante `cost_policy="free_only"`), mas a wiring real
    de produção (`app.collection.worker.run_worker`) monta o `ai_manager`
    do pipeline inteiro com `build_admin_dev_ai_provider_manager`
    (`profile=None`, só aceita `{ADMIN, DEV}`). Passar o MESMO
    `ai_manager` para o árbitro faz `CesarCoreAIProviderManager.generate`
    rejeitar a requisição com `AIRequestError` -- capturado pelo
    `except Exception` fail-closed do árbitro, então em produção ele
    SEMPRE devolvia `INCONCLUSIVE` silenciosamente, nunca decidindo de
    verdade a zona cinzenta. `arbiter_ai_manager` deixa quem monta o
    pipeline (produção) passar um manager separado construído com
    `build_user_ai_provider_manager` especificamente para o árbitro;
    quando omitido (`None`), cai de volta em `ai_manager` -- preserva o
    comportamento já coberto pelos testes existentes, cujo `ai_manager`
    fake não valida profile.

    Implementação (2026-09-20): composta de `_prepare_resolution`
    (motor determinístico/cache/reuso, sem IA) + `_finish_resolution_
    with_extraction` (zona cinzenta + grounding, depois de UMA
    extração) -- mesmo comportamento/ordem de sempre, só fatorado para
    também ser reaproveitado pelo caminho em lote de `reprocess_
    unresolved_products` (`batch_size > 1`)."""
    prepared = await _prepare_resolution(session, raw_title, now=now)
    if prepared.done:
        return prepared.resolved

    # As consultas de `_prepare_resolution` deixam uma transação
    # implícita aberta (autobegin do SQLAlchemy); sem este rollback ela
    # ficaria ociosa durante a chamada de rede à IA abaixo e seria
    # derrubada pelo `idle_in_transaction_session_timeout` do servidor
    # (achado real de 2026-09-13: `InterfaceError: cannot call
    # Transaction.commit(): underlying connection closed`, reproduzido
    # com título real e confirmado no log do Postgres). Só leituras
    # aconteceram até aqui, então não há nada a perder; a sessão reabre
    # transação sozinha na próxima operação.
    await session.rollback()
    extraction = await extract_product_identity_via_ai(
        ai_manager, raw_title=raw_title, profile=profile
    )
    if extraction is None:
        return None

    assert prepared.title_hash is not None  # sempre setado quando done=False
    return await _finish_resolution_with_extraction(
        session,
        raw_title=raw_title,
        title_hash=prepared.title_hash,
        arbitration_candidates=prepared.arbitration_candidates,
        extraction=extraction,
        ai_manager=ai_manager,
        arbiter_ai_manager=arbiter_ai_manager,
        now=now,
    )


async def apply_learned_identity(
    session: AsyncSession, *, product: Product, resolved: ResolvedProductVariant
) -> Product:
    """Aplica uma identidade já resolvida a um `Product` ad-hoc
    (`identity_key IS NULL`) -- reaproveitada tanto por `reprocess_
    unresolved_products` (lote) quanto pela Fase C do orquestrador
    (`app.collection.orchestration._persist_phase_c`, uma oferta por
    vez, dentro da seção crítica por `mission_id`).

    Nunca duplica `Product`: quando a identidade resolvida já
    corresponde a um Product CANÔNICO existente (outra oferta já
    resolveu o mesmo `identity_key` antes), todas as `Offer`s do ad-hoc
    migram para o canônico e o ad-hoc (sem nenhuma Offer restante) é
    removido; quando não existe nenhum ainda, o PRÓPRIO ad-hoc é
    promovido no lugar (ganha os campos de identidade), sem criar uma
    linha nova. Devolve o `Product` CANÔNICO resultante (o mesmo
    `product` recebido quando promovido no lugar, ou o já existente
    quando houve merge) -- comparar `.id` com o `product` original
    recebido diz ao chamador se foi merge ou promoção, sem repetir a
    consulta (usado por `reprocess_unresolved_products` para reportar
    o resultado com precisão, mesmo quando um `session.rollback()`
    posterior no mesmo processo descarta o estado da sessão).

    `pg_advisory_xact_lock` por `identity_key` (mesmo padrão de
    `app.collection.orchestration._acquire_creation_locks`) ANTES do
    `SELECT` abaixo -- achado real (2026-09-12): duas missões
    DIFERENTES coletando lojas diferentes da MESMA placa-mãe, cada uma
    aprendendo/reaproveitando o mesmo `identity_key` na Fase C do
    orquestrador (serializada só por `mission_id`, nunca globalmente),
    podiam ambas ver `canonical is None` e tentar promover seu próprio
    ad-hoc para o mesmo `identity_key` -- a segunda a commitar violava
    `uq_products_identity_key`. Sem a trava, nenhum retry local
    resolveria isso de forma limpa (a primeira transação pode nem ter
    commitado ainda quando a segunda decide)."""
    await session.execute(
        select(
            func.pg_advisory_xact_lock(func.hashtextextended(resolved.identity_key, 0))
        )
    )
    canonical = await session.scalar(
        select(Product).where(Product.identity_key == resolved.identity_key)
    )
    if canonical is not None and canonical.id != product.id:
        await session.execute(
            update(Offer)
            .where(Offer.product_id == product.id)
            .values(product_id=canonical.id)
        )
        await session.delete(product)
        return canonical
    product.category = resolved.category
    product.brand = resolved.brand
    product.model = resolved.model
    product.family = resolved.family
    product.variant = resolved.variant
    product.attributes = dict(resolved.attributes)
    product.family_key = resolved.family_key
    product.identity_key = resolved.identity_key
    product.identity_version = IDENTITY_VERSION
    return product


def _describe_resolution_outcome(
    resolved: ResolvedProductVariant, canonical: Product, original_product_id: object
) -> str:
    """Mensagem PRONTA pra reportar o resultado de UM produto -- usada
    para popular `outcome_sink` (ver docstring de `reprocess_unresolved_
    products`). `canonical.id != original_product_id` é como
    `apply_learned_identity` sinaliza merge (Offers migradas para um
    Product diferente) sem precisar de outra consulta."""
    if canonical.id != original_product_id:
        return (
            "Offers migradas para um Product canônico já existente "
            "(ad-hoc removido, nenhuma Offer perdida)"
        )
    return (
        f"RESOLVIDO -> category={resolved.category} brand={resolved.brand} "
        f"family={resolved.family} model={resolved.model} "
        f"variant={resolved.variant}"
    )


async def reprocess_unresolved_products(
    session: AsyncSession,
    *,
    ai_manager: AIProviderManager,
    profile: UserRole = UserRole.ADMIN,
    arbiter_ai_manager: AIProviderManager | None = None,
    limit: int = 100,
    batch_size: int = 1,
    apply: bool = False,
    outcome_sink: dict[object, str] | None = None,
) -> int:
    """Tenta resolver de novo `Product`s sem `identity_key` -- fallback
    ad-hoc criado por `app.collection.orchestration._resolve_offer`
    quando nenhum extrator reconheceu o título no momento da coleta
    (`Product.name` preserva o título bruto original, única evidência
    disponível para tentar de novo). Reaproveita `resolve_or_learn_
    product_variant` -- se uma definição foi aprendida DEPOIS que este
    Product ad-hoc foi criado (por este mesmo título ou por outro que
    gere o mesmo `identity_key`), ele passa a ser resolvido.

    Nunca duplica `Product`: quando a identidade resolvida já
    corresponde a um Product CANÔNICO existente (outra oferta já
    resolveu o mesmo `identity_key` antes), todas as `Offer`s do ad-hoc
    migram para o canônico e o ad-hoc (sem nenhuma Offer restante) é
    removido; quando não existe nenhum ainda, o PRÓPRIO ad-hoc é
    promovido no lugar (ganha os campos de identidade), sem criar uma
    linha nova. Devolve quantos `Product`s foram resolvidos nesta
    chamada (não o total ainda pendente -- `limit` pagina o trabalho).

    `batch_size` (padrão 1, comportamento IDÊNTICO ao de sempre --
    `resolve_or_learn_product_variant` por produto, em ordem): quando
    `> 1`, criado para TASK-123 (achado real 2026-09-20, custo de IA
    do backlog) -- primeiro resolve todos os produtos possíveis SEM IA
    (`_prepare_resolution`: motor determinístico/cache/reuso), depois
    agrupa só os que sobraram em lotes de `batch_size` e manda UMA
    chamada de IA por lote (`extract_product_identities_via_ai_batch`)
    em vez de uma por produto.

    Dois bugs REAIS encontrados e corrigidos aqui (2026-09-20, reproduzidos
    com um teste de integração real, 2 Products distintos precisando de
    extração de verdade na MESMA chamada -- nenhum teste anterior cobria
    isso, todos usavam só 1 Product):

    1. CRASH: `product.name` de um Product AINDA não processado, lido
    DEPOIS que o `session.rollback()` (de um Product anterior) já rodou,
    dispara um lazy-load síncrono fora do greenlet async e quebra --
    `session.rollback()` expira TODAS as instâncias já carregadas da
    sessão, não só a que está sendo processada no momento. Corrigido
    capturando `(id, name)` de TODOS os `unresolved` como valores simples
    ANTES do primeiro rollback; o `Product` só é relido via `session.get`
    (async-safe) na hora de aplicar.

    2. PERDA SILENCIOSA: mesmo sem crashar, `session.rollback()` desfaz a
    transação corrente INTEIRA (nunca só um SAVEPOINT) -- se um Product
    já tinha sido resolvido E aplicado (mas ainda não commitado) quando
    outro Product seguinte precisa de nova chamada de IA, o rollback
    daquele segundo Product apagaria o trabalho do primeiro. Só importa
    quando o chamador realmente PRETENDE persistir (`apply=True`); em
    dry-run, perder trabalho intermediário é inofensivo (nada deveria
    sobreviver mesmo). Corrigido commitando IMEDIATAMENTE depois de cada
    `apply_learned_identity` bem-sucedido, sempre que `apply=True` -- só
    então o próximo rollback (do próximo Product/lote) nunca mais alcança
    esse trabalho.

    3. RELATÓRIO CONTRADITÓRIO em `--dry-run` (achado real em PROD,
    2026-09-20, `v1.3.20`): o bug 2 acima só protege `apply=True`. Em
    `--dry-run` (`apply=False`, de propósito, nada é commitado), um
    Product resolvido num lote/iteração ainda aparece neste `resolved_
    count` (contador Python simples), mas o `session.rollback()` de um
    lote SEGUINTE (mesmo que aquele lote seguinte falhe na própria
    chamada de IA -- o rollback roda incondicionalmente ANTES da
    chamada) desfaz esse Product da SESSÃO -- reconsultar via `session.
    get` depois (como o script fazia) mostra "sem identidade" para um
    Product que este `resolved_count` já contou como resolvido.
    Corrigido com `outcome_sink`: quando o chamador passa um `dict`
    vazio, cada resolução bem-sucedida grava ali uma mensagem PRONTA
    (`_describe_resolution_outcome`) no momento em que acontece --
    nunca depende de reconsultar o banco depois de um rollback que pode
    ter descartado o estado. `outcome_sink=None` (padrão) preserva o
    comportamento anterior para quem não precisa desse detalhe."""
    unresolved = (
        await session.scalars(
            select(Product).where(Product.identity_key.is_(None)).limit(limit)
        )
    ).all()
    # Capturados como valores simples AQUI, antes de qualquer rollback --
    # ver bug 1 acima.
    items = [(product.id, product.name) for product in unresolved]

    if batch_size <= 1:
        resolved_count = 0
        for product_id, raw_title in items:
            resolved = await resolve_or_learn_product_variant(
                session,
                raw_title=raw_title,
                ai_manager=ai_manager,
                profile=profile,
                arbiter_ai_manager=arbiter_ai_manager,
            )
            if resolved is None:
                continue
            product = await session.get(Product, product_id)
            if product is None or product.identity_key is not None:
                continue
            canonical = await apply_learned_identity(
                session, product=product, resolved=resolved
            )
            resolved_count += 1
            if outcome_sink is not None:
                outcome_sink[product_id] = _describe_resolution_outcome(
                    resolved, canonical, product_id
                )
            if apply:
                await session.commit()
        return resolved_count

    resolved_count = 0
    pending: list[tuple[object, str, _PreparedResolution]] = []
    for product_id, raw_title in items:
        prepared = await _prepare_resolution(session, raw_title)
        if prepared.done:
            if prepared.resolved is not None:
                product = await session.get(Product, product_id)
                if product is not None and product.identity_key is None:
                    canonical = await apply_learned_identity(
                        session, product=product, resolved=prepared.resolved
                    )
                    resolved_count += 1
                    if outcome_sink is not None:
                        outcome_sink[product_id] = _describe_resolution_outcome(
                            prepared.resolved, canonical, product_id
                        )
                    if apply:
                        await session.commit()
            continue
        pending.append((product_id, raw_title, prepared))

    for start in range(0, len(pending), batch_size):
        chunk = pending[start : start + batch_size]
        # Mesmo motivo do rollback em `resolve_or_learn_product_variant`:
        # nunca segurar transação ociosa durante a chamada de rede à IA.
        # Seguro por causa do commit acima (bug 2): tudo que já foi
        # aplicado com `apply=True` já é durável antes deste ponto.
        await session.rollback()
        extractions = await extract_product_identities_via_ai_batch(
            ai_manager,
            raw_titles=[raw_title for _, raw_title, _ in chunk],
            profile=profile,
        )
        for (product_id, raw_title, prepared), extraction in zip(
            chunk, extractions, strict=True
        ):
            if extraction is None:
                continue
            resolved = await _finish_resolution_with_extraction(
                session,
                raw_title=raw_title,
                title_hash=prepared.title_hash,
                arbitration_candidates=prepared.arbitration_candidates,
                extraction=extraction,
                ai_manager=ai_manager,
                arbiter_ai_manager=arbiter_ai_manager,
            )
            if resolved is None:
                continue
            product = await session.get(Product, product_id)
            if product is None or product.identity_key is not None:
                # Corrida: outro processo já resolveu/removeu este
                # Product entre a montagem do lote e agora -- nunca
                # reaplica nem propaga erro, só não conta de novo.
                continue
            canonical = await apply_learned_identity(
                session, product=product, resolved=resolved
            )
            resolved_count += 1
            if outcome_sink is not None:
                outcome_sink[product_id] = _describe_resolution_outcome(
                    resolved, canonical, product_id
                )
            if apply:
                await session.commit()
    return resolved_count
