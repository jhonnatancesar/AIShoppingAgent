"""Consumo de cupons pelo GG Oferta -- só schema/leitura nesta fase, sem
aplicabilidade, F2/F3, alerta, Telegram ou frontend (decisão de
arquitetura de 2026-09-06)."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from app.core.config import Settings
from app.coupons.models import Coupon, CouponOfferLink
from app.coupons.pricing import best_applicable_coupon
from app.coupons.service import (
    get_candidate_coupons_for_offer,
    get_coupons_for_offer,
    get_unlinked_coupons_for_store,
    list_active_coupons,
)
from app.offers.models import Offer
from app.products.identity import IDENTITY_VERSION, resolve_product_variant
from app.products.models import Product
from app.stores.models import Store
from app.webapp.coupons_router import list_coupons
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration
NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


def _store(sessions, *, code: str) -> Store:
    with sessions() as session:
        return session.scalar(select(Store).where(Store.code == code))


def _product(sessions, *, title: str = "NVIDIA GeForce RTX 5070 Ti") -> Product:
    variant = resolve_product_variant(title)
    assert variant is not None
    with sessions.begin() as session:
        product = Product(
            name=title,
            brand=variant.brand,
            model=variant.model,
            category=variant.category,
            family=variant.family,
            variant=variant.variant,
            attributes=dict(variant.attributes),
            family_key=variant.family_key,
            identity_key=variant.identity_key,
            identity_version=IDENTITY_VERSION,
        )
        session.add(product)
        session.flush()
        session.expunge(product)
        return product


def _offer(sessions, *, product_id, store_id) -> Offer:
    with sessions.begin() as session:
        offer = Offer(
            product_id=product_id,
            store_id=store_id,
            url=f"https://example.invalid/coupon-test-{uuid4().hex[:12]}",
        )
        session.add(offer)
        session.flush()
        session.expunge(offer)
        return offer


def _coupon(
    sessions,
    *,
    store_id,
    code: str = "",
    evidence: str = "Cupom de R$ 20,00 de desconto",
    status: str = "active",
    scope_kind: str | None = None,
    scope_reference: str | None = None,
    discount_kind: str | None = "fixed_amount",
    discount_value: Decimal | None = Decimal("20.00"),
    minimum_purchase_amount: Decimal | None = None,
    raw_rule_text: str | None = None,
    valid_until: str | None = None,
    last_seen_at: datetime | None = None,
    source_url: str | None = None,
) -> Coupon:
    with sessions.begin() as session:
        coupon = Coupon(
            store_id=store_id,
            code=code,
            discount_kind=discount_kind,
            discount_value=discount_value,
            minimum_purchase_amount=minimum_purchase_amount,
            raw_rule_text=raw_rule_text,
            valid_until=valid_until,
            evidence=evidence,
            status=status,
            scope_kind=scope_kind,
            scope_reference=scope_reference,
            last_seen_at=last_seen_at or NOW,
            source_url=source_url,
        )
        session.add(coupon)
        session.flush()
        session.expunge(coupon)
        return coupon


def test_coupon_dedup_by_store_code_evidence(integration_database) -> None:
    store = _store(integration_database.sessions, code="kabum")
    _coupon(integration_database.sessions, store_id=store.id, code="GAMER10")
    with pytest.raises(IntegrityError):
        _coupon(integration_database.sessions, store_id=store.id, code="GAMER10")


def test_coupon_without_code_deduplicates_by_evidence(integration_database) -> None:
    """`code=""` (nunca `NULL`) -- confirma que dois cupons sem código mas
    com evidências DIFERENTES coexistem, e a mesma evidência duplica."""
    store = _store(integration_database.sessions, code="amazon")
    _coupon(integration_database.sessions, store_id=store.id, evidence="Você paga R$ 100 com o cupom")
    # Evidência diferente -- linha nova, sem conflito.
    _coupon(integration_database.sessions, store_id=store.id, evidence="Você paga R$ 200 com o cupom")
    with integration_database.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Coupon)
                .where(Coupon.store_id == store.id)
            )
            == 2
        )


def test_coupon_offer_link_unique_pair(integration_database) -> None:
    store = _store(integration_database.sessions, code="magalu")
    product = _product(integration_database.sessions)
    offer = _offer(integration_database.sessions, product_id=product.id, store_id=store.id)
    coupon = _coupon(integration_database.sessions, store_id=store.id, code="MAGA15")

    with integration_database.sessions.begin() as session:
        session.add(CouponOfferLink(coupon_id=coupon.id, offer_id=offer.id))

    # Mesma dupla (coupon, offer) de novo -- deve violar a constraint.
    with pytest.raises(IntegrityError):
        with integration_database.sessions.begin() as session:
            session.add(CouponOfferLink(coupon_id=coupon.id, offer_id=offer.id))


def test_deleting_offer_removes_only_the_link_never_the_coupon(
    integration_database,
) -> None:
    """Correção explícita do usuário: `offer_id` usa `ondelete="CASCADE"`
    -- apagar uma Offer nunca pode ficar bloqueado só por ter um cupom
    associado, e nunca pode apagar o Coupon (que não tem FK direta pra
    Offer)."""
    store = _store(integration_database.sessions, code="magalu")
    product = _product(integration_database.sessions, title="NVIDIA GeForce RTX 5070")
    offer = _offer(integration_database.sessions, product_id=product.id, store_id=store.id)
    coupon = _coupon(integration_database.sessions, store_id=store.id, code="MAGA20")

    with integration_database.sessions.begin() as session:
        session.add(CouponOfferLink(coupon_id=coupon.id, offer_id=offer.id))

    # Apagar a Offer TEM que funcionar -- nunca bloqueado por ter um
    # cupom vinculado.
    with integration_database.sessions.begin() as session:
        session.delete(session.get(Offer, offer.id))

    with integration_database.sessions() as session:
        assert session.get(Offer, offer.id) is None
        # O vínculo desapareceu (cascade)...
        assert (
            session.scalar(
                select(func.count())
                .select_from(CouponOfferLink)
                .where(CouponOfferLink.offer_id == offer.id)
            )
            == 0
        )
        # ...mas o Coupon em si sobrevive intacto.
        surviving = session.get(Coupon, coupon.id)
        assert surviving is not None
        assert surviving.code == "MAGA20"


def test_get_coupons_for_offer_only_returns_linked_active(integration_database) -> None:
    store = _store(integration_database.sessions, code="mercadolivre")
    product = _product(integration_database.sessions, title="NVIDIA GeForce RTX 5060")
    offer = _offer(integration_database.sessions, product_id=product.id, store_id=store.id)
    linked_active = _coupon(integration_database.sessions, store_id=store.id, code="ML10")
    linked_expired = _coupon(
        integration_database.sessions, store_id=store.id, code="ML_OLD", status="expired"
    )
    unlinked = _coupon(integration_database.sessions, store_id=store.id, code="ML_GENERIC")

    with integration_database.sessions.begin() as session:
        session.add(CouponOfferLink(coupon_id=linked_active.id, offer_id=offer.id))
        session.add(CouponOfferLink(coupon_id=linked_expired.id, offer_id=offer.id))

    async def _fetch():
        async with integration_database.async_sessions() as session:
            return await get_coupons_for_offer(session, offer_id=offer.id)

    result = asyncio.run(_fetch())
    assert {c.id for c in result} == {linked_active.id}
    assert unlinked.id not in {c.id for c in result}


def test_get_unlinked_coupons_for_store_excludes_linked_and_other_stores(
    integration_database,
) -> None:
    store = _store(integration_database.sessions, code="pichau")
    other_store = _store(integration_database.sessions, code="terabyte")
    product = _product(integration_database.sessions, title="NVIDIA GeForce RTX 5080")
    offer = _offer(integration_database.sessions, product_id=product.id, store_id=store.id)

    generic = _coupon(
        integration_database.sessions,
        store_id=store.id,
        code="PICHAU5",
        scope_kind="store_wide",
    )
    linked = _coupon(integration_database.sessions, store_id=store.id, code="PICHAU_LINKED")
    other = _coupon(integration_database.sessions, store_id=other_store.id, code="TERA5")
    # Cupom genérico (sem vínculo) mas já inativo -- não pode aparecer
    # como candidato, seja por ter "sumido" (expire_stale do worker) ou
    # por ter sido detectado esgotado/encerrado (evidence.py).
    expired_generic = _coupon(
        integration_database.sessions, store_id=store.id, code="PICHAU_EXPIRED", status="expired"
    )

    with integration_database.sessions.begin() as session:
        session.add(CouponOfferLink(coupon_id=linked.id, offer_id=offer.id))

    async def _fetch():
        async with integration_database.async_sessions() as session:
            return await get_unlinked_coupons_for_store(session, store_id=store.id)

    result = asyncio.run(_fetch())
    ids = {c.id for c in result}
    assert generic.id in ids
    assert linked.id not in ids
    assert other.id not in ids
    assert expired_generic.id not in ids
    assert result[0].scope_kind == "store_wide"  # preservado cru, sem parsing


def test_expired_coupon_row_survives_as_history_never_deleted(
    integration_database,
) -> None:
    """Registro histórico: um cupom que deixou de ser utilizável (por
    qualquer motivo -- sumiu da fonte ou foi detectado esgotado) nunca é
    apagado do banco, só marcado `expired`. Ele desaparece das consultas
    de candidatos do GG, mas continua existindo/consultável diretamente."""
    store = _store(integration_database.sessions, code="kabum")
    coupon = _coupon(
        integration_database.sessions, store_id=store.id, code="KABUM_HIST", status="expired"
    )

    with integration_database.sessions() as session:
        row = session.get(Coupon, coupon.id)
        assert row is not None
        assert row.status == "expired"

    async def _fetch():
        async with integration_database.async_sessions() as session:
            return await get_unlinked_coupons_for_store(session, store_id=store.id)

    result = asyncio.run(_fetch())
    assert coupon.id not in {c.id for c in result}  # não é candidato...
    with integration_database.sessions() as session:
        assert session.get(Coupon, coupon.id) is not None  # ...mas nunca foi apagado


# ---------------------------------------------------------------------------
# Consumo real (2026-09-06): get_candidate_coupons_for_offer + pricing.py
# contra linhas reais do banco (não só objetos construídos em memória).
# ---------------------------------------------------------------------------


def test_get_candidate_coupons_for_offer_unions_linked_and_unlinked_without_duplicates(
    integration_database,
) -> None:
    store = _store(integration_database.sessions, code="amazon")
    product = _product(integration_database.sessions, title="NVIDIA GeForce RTX 4070")
    offer = _offer(integration_database.sessions, product_id=product.id, store_id=store.id)
    linked = _coupon(integration_database.sessions, store_id=store.id, code="AMZ_LINKED")
    generic = _coupon(
        integration_database.sessions,
        store_id=store.id,
        code="AMZ_GENERIC",
        scope_kind="store_wide",
    )
    other_store = _store(integration_database.sessions, code="kabum")
    _coupon(integration_database.sessions, store_id=other_store.id, code="KABUM_ONLY")

    with integration_database.sessions.begin() as session:
        session.add(CouponOfferLink(coupon_id=linked.id, offer_id=offer.id))

    async def _fetch():
        async with integration_database.async_sessions() as session:
            return await get_candidate_coupons_for_offer(
                session, offer_id=offer.id, store_id=store.id
            )

    result = asyncio.run(_fetch())
    ids = [c.id for c in result]
    assert set(ids) == {linked.id, generic.id}
    assert len(ids) == len(set(ids))  # sem duplicata mesmo vindo de duas consultas


def test_best_applicable_coupon_end_to_end_store_wide_against_real_rows(
    integration_database,
) -> None:
    store = _store(integration_database.sessions, code="magalu")
    product = _product(integration_database.sessions, title="NVIDIA GeForce RTX 4060")
    offer = _offer(integration_database.sessions, product_id=product.id, store_id=store.id)
    _coupon(
        integration_database.sessions,
        store_id=store.id,
        code="MAGA_WIDE",
        scope_kind="store_wide",
    )

    async def _fetch():
        async with integration_database.async_sessions() as session:
            offer_row = await session.get(Offer, offer.id)
            candidates = await get_candidate_coupons_for_offer(
                session, offer_id=offer.id, store_id=store.id
            )
            return best_applicable_coupon(offer_row, candidates, Decimal("300.00"), "BRL")

    applied = asyncio.run(_fetch())
    assert applied is not None
    assert applied.code == "MAGA_WIDE"
    assert applied.final_amount == Decimal("280.00")


def test_best_applicable_coupon_end_to_end_product_scope_requires_exact_url(
    integration_database,
) -> None:
    store = _store(integration_database.sessions, code="pichau")
    product = _product(integration_database.sessions, title="NVIDIA GeForce RTX 4080")
    offer = _offer(integration_database.sessions, product_id=product.id, store_id=store.id)
    other_offer = _offer(
        integration_database.sessions, product_id=product.id, store_id=store.id
    )
    _coupon(
        integration_database.sessions,
        store_id=store.id,
        code="PICHAU_PROD",
        scope_kind="product",
        scope_reference=offer.url,
    )

    async def _fetch(target_offer_id):
        async with integration_database.async_sessions() as session:
            offer_row = await session.get(Offer, target_offer_id)
            candidates = await get_candidate_coupons_for_offer(
                session, offer_id=target_offer_id, store_id=store.id
            )
            return best_applicable_coupon(offer_row, candidates, Decimal("300.00"), "BRL")

    assert asyncio.run(_fetch(offer.id)) is not None
    # Mesma Store, mesmo Product, URL DIFERENTE -- nunca aplica por
    # semelhança de nome/path, só igualdade exata pós-normalização.
    assert asyncio.run(_fetch(other_offer.id)) is None


# ---------------------------------------------------------------------------
# TASK-121: listagem geral de cupons ativos (aba "Cupons") -- propósito
# diferente de tudo acima (aplicabilidade cupom<->Offer). `list_active_
# coupons` (service) e `list_coupons` (endpoint) contra PostgreSQL real.
# ---------------------------------------------------------------------------


def test_list_active_coupons_only_returns_active(integration_database) -> None:
    store = _store(integration_database.sessions, code="kabum")
    active = _coupon(integration_database.sessions, store_id=store.id, code="ATIVO1")
    _coupon(integration_database.sessions, store_id=store.id, code="EXPIRADO1", status="expired")

    async def _fetch():
        async with integration_database.async_sessions() as session:
            return await list_active_coupons(session)

    result = asyncio.run(_fetch())
    ids = {coupon.id for coupon, _store_row in result}
    assert active.id in ids
    assert not any(coupon.code == "EXPIRADO1" for coupon, _store_row in result)


def test_list_active_coupons_orders_by_last_seen_at_desc(integration_database) -> None:
    store = _store(integration_database.sessions, code="amazon")
    older = _coupon(
        integration_database.sessions,
        store_id=store.id,
        code="MAIS_ANTIGO",
        last_seen_at=NOW - timedelta(hours=2),
    )
    newer = _coupon(
        integration_database.sessions,
        store_id=store.id,
        code="MAIS_RECENTE",
        last_seen_at=NOW,
    )

    async def _fetch():
        async with integration_database.async_sessions() as session:
            return await list_active_coupons(session)

    result = asyncio.run(_fetch())
    ids_in_order = [coupon.id for coupon, _store_row in result]
    assert ids_in_order.index(newer.id) < ids_in_order.index(older.id)


def test_list_active_coupons_respects_defensive_limit(integration_database) -> None:
    store = _store(integration_database.sessions, code="magalu")
    for i in range(5):
        _coupon(
            integration_database.sessions,
            store_id=store.id,
            code=f"LIMITE{i}",
            evidence=f"Evidência única {i}",
            last_seen_at=NOW - timedelta(minutes=i),
        )

    async def _fetch():
        async with integration_database.async_sessions() as session:
            return await list_active_coupons(session, limit=3)

    result = asyncio.run(_fetch())
    assert len(result) == 3
    # Respeitando a ordenação -- os 3 mais recentes, nunca os 3 primeiros
    # inseridos por acaso.
    codes_returned = [coupon.code for coupon, _store_row in result]
    assert codes_returned == ["LIMITE0", "LIMITE1", "LIMITE2"]


def test_list_active_coupons_single_query_no_n_plus_one(integration_database) -> None:
    """`Coupon` não tem `relationship()` pra `Store` -- join explícito
    numa única query. Prova real via contagem de SELECTs executados, não
    só leitura de código."""
    store = _store(integration_database.sessions, code="pichau")
    for i in range(4):
        _coupon(
            integration_database.sessions,
            store_id=store.id,
            code=f"NPLUS1_{i}",
            evidence=f"Evidência N+1 {i}",
        )

    counter = {"n": 0}

    def before_cursor_execute(*_args, **_kwargs):
        counter["n"] += 1

    engine = integration_database.async_engine.sync_engine
    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        async def _fetch():
            async with integration_database.async_sessions() as session:
                return await list_active_coupons(session)

        result = asyncio.run(_fetch())
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)

    assert len(result) == 4
    assert counter["n"] == 1  # uma única query, nunca uma por cupom


def test_list_active_coupons_preserves_absent_fields_as_none(integration_database) -> None:
    """Nenhum campo opcional ausente é inventado -- `None` chega como
    `None` até o fim, nunca um valor default fabricado."""
    store = _store(integration_database.sessions, code="terabyte")
    coupon = _coupon(
        integration_database.sessions,
        store_id=store.id,
        code="SEM_DESCONTO_ESTRUTURADO",
        discount_kind=None,
        discount_value=None,
        minimum_purchase_amount=None,
        raw_rule_text=None,
        valid_until=None,
        scope_kind=None,
    )

    async def _fetch():
        async with integration_database.async_sessions() as session:
            return await list_active_coupons(session)

    result = asyncio.run(_fetch())
    match = next(c for c, _store_row in result if c.id == coupon.id)
    assert match.discount_kind is None
    assert match.discount_value is None
    assert match.minimum_purchase_amount is None
    assert match.raw_rule_text is None
    assert match.valid_until is None
    assert match.scope_kind is None


def test_list_active_coupons_returns_store_explicitly(integration_database) -> None:
    store = _store(integration_database.sessions, code="mercadolivre")
    coupon = _coupon(integration_database.sessions, store_id=store.id, code="LOJA_EXPLICITA")

    async def _fetch():
        async with integration_database.async_sessions() as session:
            return await list_active_coupons(session)

    result = asyncio.run(_fetch())
    match = next((c, s) for c, s in result if c.id == coupon.id)
    _matched_coupon, matched_store = match
    assert matched_store.id == store.id
    assert matched_store.code == "mercadolivre"
    assert matched_store.name == store.name


# ---------------------------------------------------------------------------
# Endpoint (`webapp.coupons_router.list_coupons`) chamado diretamente como
# função async real -- os parâmetros `Depends(...)` são só defaults do
# FastAPI, nunca impedem a chamada direta com valores explícitos. `user`
# não é usado no corpo da função (só gate de autenticação via
# `require_web_session`, já testado em outro lugar) -- passar `None` é
# seguro e não mascara nenhum comportamento real do endpoint.
# ---------------------------------------------------------------------------


def test_list_coupons_endpoint_returns_active_with_real_fields(integration_database) -> None:
    store = _store(integration_database.sessions, code="kabum")
    coupon = _coupon(
        integration_database.sessions,
        store_id=store.id,
        code="ENDPOINT_REAL",
        discount_kind="percentage",
        discount_value=Decimal("10.00"),
        minimum_purchase_amount=Decimal("50.00"),
        raw_rule_text="10% OFF em toda a loja",
        valid_until="31/12/2026",
        scope_kind="store_wide",
        source_url="https://www.kabum.com.br/cupons",
    )
    _coupon(
        integration_database.sessions, store_id=store.id, code="ENDPOINT_EXPIRADO", status="expired"
    )

    async def _fetch():
        async with integration_database.async_sessions() as session:
            return await list_coupons(
                user=None,  # type: ignore[arg-type]
                session=session,
                settings=Settings(coupons_enabled=True),
            )

    result = asyncio.run(_fetch())
    ids = {out.id for out in result}
    assert coupon.id in ids
    assert not any(out.code == "ENDPOINT_EXPIRADO" for out in result)

    match = next(out for out in result if out.id == coupon.id)
    assert match.store.code == "kabum"
    assert match.store.name == store.name
    assert match.code == "ENDPOINT_REAL"
    assert match.discount_kind == "percentage"
    assert match.discount_value == Decimal("10.00")
    assert match.minimum_purchase_amount == Decimal("50.00")
    assert match.raw_rule_text == "10% OFF em toda a loja"
    assert match.valid_until == "31/12/2026"  # texto cru, nunca parseado
    assert match.scope_kind == "store_wide"
    assert match.source_url == "https://www.kabum.com.br/cupons"
    assert match.last_seen_at is not None


def test_list_coupons_endpoint_returns_empty_code_as_none(integration_database) -> None:
    """`Coupon.code` é `""` (nunca `NULL`) quando não há código próprio --
    o endpoint normaliza pra `None`, nunca expõe string vazia."""
    store = _store(integration_database.sessions, code="amazon")
    coupon = _coupon(integration_database.sessions, store_id=store.id, code="")

    async def _fetch():
        async with integration_database.async_sessions() as session:
            return await list_coupons(
                user=None,  # type: ignore[arg-type]
                session=session,
                settings=Settings(coupons_enabled=True),
            )

    result = asyncio.run(_fetch())
    match = next(out for out in result if out.id == coupon.id)
    assert match.code is None


def test_list_coupons_endpoint_source_url_absent_stays_none(integration_database) -> None:
    """Sem `source_url` capturado, o campo chega `None` na API -- nunca
    uma URL fabricada a partir do nome da loja/código/slug."""
    store = _store(integration_database.sessions, code="magalu")
    coupon = _coupon(integration_database.sessions, store_id=store.id, code="", source_url=None)

    async def _fetch():
        async with integration_database.async_sessions() as session:
            return await list_coupons(
                user=None,  # type: ignore[arg-type]
                session=session,
                settings=Settings(coupons_enabled=True),
            )

    result = asyncio.run(_fetch())
    match = next(out for out in result if out.id == coupon.id)
    assert match.source_url is None


def test_list_coupons_endpoint_flag_disabled_returns_empty_even_with_data(
    integration_database,
) -> None:
    store = _store(integration_database.sessions, code="magalu")
    _coupon(integration_database.sessions, store_id=store.id, code="FLAG_DESLIGADA")

    async def _fetch():
        async with integration_database.async_sessions() as session:
            return await list_coupons(
                user=None,  # type: ignore[arg-type]
                session=session,
                settings=Settings(coupons_enabled=False),
            )

    result = asyncio.run(_fetch())
    assert result == []
