"""TASK-133 (etapa 1) -- falha de IA de identidade sempre deixa um candidato,
contra Postgres real: espera antes de tentar de novo, teto só para falha de
conteúdo, disjuntor e substituição pela decisão real."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.ai_provider import AIProviderQuotaExceeded, AIResponse
from app.products.identity_ai import normalized_title_hash
from app.products.identity_ai_failure import AI_BREAKER
from app.products.identity_candidates import ProductIdentityCandidate
from app.products.identity_learning import resolve_or_learn_product_variant
from app.products.models import Product
from app.users.models import UserRole
from sqlalchemy import func, select, text

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
TITLE = "Módulo de memória Marca Desconhecida XYZ 8GB DDR5 6000MHz"
GOOD = json.dumps(
    {
        "category": "ram",
        "brand": "Kingston",
        "family": "Fury Beast",
        "model": "KF560C36BBE-8",
        "variant": None,
        "store_sku": None,
        "manufacturer_part_number": "KF560C36BBE-8",
        "attributes": {},
    }
)


class _AI:
    """`content=None` levanta `error`; senão devolve o conteúdo."""

    def __init__(self, content: str | None = None, error: Exception | None = None):
        self.content = content
        self.error = error
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub-identity-model",
            content=self.content,
            finished_at=datetime.now(UTC),
        )


def _close_breaker(integration_database) -> None:
    """Simula a passagem do tempo: fecha o disjuntor local e o compartilhado."""
    AI_BREAKER.reset()
    with integration_database.sessions.begin() as session:
        session.execute(text("DELETE FROM identity_ai_breaker"))


def _resolve(integration_database, ai, now, title=TITLE, source_product_id=None):
    async def _run():
        async with integration_database.async_sessions() as session:
            resolved = await resolve_or_learn_product_variant(
                session,
                raw_title=title,
                ai_manager=ai,
                profile=UserRole.ADMIN,
                now=now,
                source_product_id=source_product_id,
            )
            await session.commit()
            return resolved

    return asyncio.run(_run())


def _candidate(integration_database, title=TITLE) -> ProductIdentityCandidate | None:
    with integration_database.sessions() as session:
        return session.scalar(
            select(ProductIdentityCandidate).where(
                ProductIdentityCandidate.normalized_title_hash
                == normalized_title_hash(title)
            )
        )


def test_failure_leaves_a_candidate_and_waits_before_calling_the_ai_again(
    integration_database,
) -> None:
    ai = _AI(error=RuntimeError("provedor fora"))
    assert _resolve(integration_database, ai, T0) is None
    row = _candidate(integration_database)
    assert (row.status, row.ai_error_kind, row.ai_attempts) == (
        "ai_failed",
        "provider_error",
        1,
    )
    assert row.next_retry_at == T0 + timedelta(minutes=15)
    assert ai.calls == 1

    # antes do prazo: nenhuma chamada nova (acaba o laço de uma chamada por coleta)
    assert _resolve(integration_database, ai, T0 + timedelta(minutes=5)) is None
    assert ai.calls == 1

    # depois do prazo: tenta, falha de novo, espera mais e conta a tentativa
    _close_breaker(integration_database)
    assert _resolve(integration_database, ai, T0 + timedelta(minutes=16)) is None
    row = _candidate(integration_database)
    assert (ai.calls, row.ai_attempts) == (2, 2)
    assert row.next_retry_at == T0 + timedelta(minutes=16) + timedelta(minutes=30)


def test_real_answer_replaces_the_failure_row(integration_database) -> None:
    grounded = "Memória Kingston Fury Beast 8GB DDR5 6000MHz KF560C36BBE-8"
    down = _AI(error=RuntimeError("fora"))
    _resolve(integration_database, down, T0, title=grounded)
    _close_breaker(integration_database)

    up = _AI(GOOD)
    resolved = _resolve(
        integration_database, up, T0 + timedelta(hours=1), title=grounded
    )

    assert resolved is not None and resolved.family == "fury-beast"
    row = _candidate(integration_database, grounded)
    assert row.status == "approved"
    with integration_database.sessions() as session:
        total = session.scalar(
            select(func.count()).select_from(ProductIdentityCandidate)
        )
    assert total == 1, "a linha de falha foi substituída, nunca duplicada"


def test_content_failures_hit_the_cap_and_go_to_page_reading(
    integration_database,
) -> None:
    with integration_database.sessions.begin() as session:
        product = Product(id=uuid4(), name=TITLE)
        session.add(product)
        product_id = product.id
    garbage = _AI("isto não é JSON")
    for n in range(5):
        _resolve(
            integration_database,
            garbage,
            T0 + timedelta(days=2 * n),
            source_product_id=product_id,
        )
    row = _candidate(integration_database)
    assert row.status == "awaiting_page", "no teto, segue para a leitura de página"
    assert row.source_product_id == product_id
    assert (row.ai_error_kind, row.next_retry_at) == (None, None)
    assert garbage.calls == 5


def test_content_cap_without_a_source_product_is_terminal_and_visible(
    integration_database,
) -> None:
    garbage = _AI("isto não é JSON")
    for n in range(5):
        _resolve(integration_database, garbage, T0 + timedelta(days=2 * n))
    assert _candidate(integration_database).status == "unrecognized"


def test_infrastructure_failures_never_reach_the_cap(integration_database) -> None:
    quota = _AI(error=AIProviderQuotaExceeded())
    for n in range(8):
        _close_breaker(integration_database)
        _resolve(integration_database, quota, T0 + timedelta(days=n))
    row = _candidate(integration_database)
    assert (row.status, row.ai_error_kind, row.ai_attempts) == ("ai_failed", "quota", 8)
    assert row.next_retry_at == T0 + timedelta(days=7) + timedelta(hours=6)


def test_breaker_pauses_the_ai_for_every_title_and_still_leaves_candidates(
    integration_database,
) -> None:
    quota = _AI(error=AIProviderQuotaExceeded())
    _resolve(integration_database, quota, T0)
    assert quota.calls == 1
    other = "Outra memória sem marca conhecida ABC 16GB DDR4 3200MHz"
    # o disjuntor está aberto: o segundo título nem chama a IA, mas ganha candidato
    assert _resolve(integration_database, quota, T0, title=other) is None
    assert quota.calls == 1
    row = _candidate(integration_database, other)
    assert (row.status, row.ai_error_kind, row.ai_attempts) == (
        "ai_failed",
        "circuit_open",
        0,
    )
    assert row.next_retry_at is not None


def test_page_sweep_refunds_the_attempt_when_the_ai_is_down(
    integration_database,
) -> None:
    from app.collection.contracts import ProductPageRead, ProductPageReadStatus
    from app.offers.models import Offer
    from app.products.identity_page import resolve_awaiting_page_titles
    from app.stores.models import Store

    title = "Cadeira misteriosa sem nenhuma pista"
    with integration_database.sessions.begin() as session:
        store = session.scalar(select(Store).where(Store.code == "amazon"))
        product = Product(id=uuid4(), name=title)
        session.add(product)
        session.flush()
        session.add(
            Offer(
                product_id=product.id,
                store_id=store.id,
                external_id="PAGE-1",
                url="https://amazon.example.test/dp/PAGE-1",
            )
        )
        session.add(
            ProductIdentityCandidate(
                id=uuid4(),
                raw_title=title,
                normalized_title_hash=normalized_title_hash(title),
                status="awaiting_page",
                grounded=False,
                source_product_id=product.id,
                created_at=T0,
            )
        )

    async def reader(store_code, url):
        return ProductPageRead(ProductPageReadStatus.READ, context="Categoria: Cadeira")

    quota = _AI(error=AIProviderQuotaExceeded())
    for n in range(4):
        _close_breaker(integration_database)
        asyncio.run(
            resolve_awaiting_page_titles(
                integration_database.async_sessions,
                page_reader=reader,
                ai_manager=quota,
                budget=3,
                now=T0 + timedelta(days=n),
            )
        )
    row = _candidate(integration_database, title)
    assert row.status == "awaiting_page", "queda de IA nunca vira terminal"
    assert row.page_attempts == 0
    assert quota.calls == 4


def test_breaker_is_shared_across_processes_and_survives_restart(
    integration_database,
) -> None:
    quota = _AI(error=AIProviderQuotaExceeded())
    _resolve(integration_database, quota, T0)
    assert quota.calls == 1

    # "outro processo" ou restart: a memória local é zerada, o banco continua
    AI_BREAKER.reset()
    other = "Outra memória sem marca conhecida ABC 16GB DDR4 3200MHz"
    assert _resolve(integration_database, quota, T0, title=other) is None
    assert quota.calls == 1, "o disjuntor compartilhado pausou a IA"

    with integration_database.sessions() as session:
        stored = session.execute(
            text("SELECT id, reason, open_until FROM identity_ai_breaker")
        ).one()
    assert (stored.id, stored.reason) == (1, "quota")
    assert stored.open_until > datetime.now(UTC) - timedelta(days=1)


def test_page_sweep_honors_the_shared_breaker_without_opening_the_page(
    integration_database,
) -> None:
    from app.collection.contracts import ProductPageRead, ProductPageReadStatus
    from app.offers.models import Offer
    from app.products.identity_ai import AIExtractionFailure
    from app.products.identity_ai_failure import breaker_trip
    from app.products.identity_page import resolve_awaiting_page_titles
    from app.stores.models import Store

    title = "Cadeira outra misteriosa"
    with integration_database.sessions.begin() as session:
        store = session.scalar(select(Store).where(Store.code == "amazon"))
        product = Product(id=uuid4(), name=title)
        session.add(product)
        session.flush()
        session.add(
            Offer(
                product_id=product.id,
                store_id=store.id,
                external_id="PAGE-2",
                url="https://amazon.example.test/dp/PAGE-2",
            )
        )
        session.add(
            ProductIdentityCandidate(
                id=uuid4(),
                raw_title=title,
                normalized_title_hash=normalized_title_hash(title),
                status="awaiting_page",
                grounded=False,
                source_product_id=product.id,
                created_at=T0,
            )
        )

    async def _trip():
        async with integration_database.async_sessions() as session:
            await breaker_trip(session, AIExtractionFailure("quota"), datetime.now(UTC))
            await session.commit()

    asyncio.run(_trip())
    AI_BREAKER.reset()
    opened: list[str] = []

    async def reader(store_code, url):
        opened.append(url)
        return ProductPageRead(ProductPageReadStatus.READ, context="Categoria: x")

    ai = _AI(GOOD)
    asyncio.run(
        resolve_awaiting_page_titles(
            integration_database.async_sessions,
            page_reader=reader,
            ai_manager=ai,
            budget=3,
            now=datetime.now(UTC),
        )
    )
    assert opened == [] and ai.calls == 0
    assert _candidate(integration_database, title).page_attempts == 0


def test_breaker_write_survives_a_rollback_of_the_caller(integration_database) -> None:
    from app.products.identity_ai import AIExtractionFailure
    from app.products.identity_ai_failure import breaker_trip

    async def run():
        async with integration_database.async_sessions() as session:
            await breaker_trip(session, AIExtractionFailure("quota"), datetime.now(UTC))
            await session.rollback()  # o chamador desiste: o aviso já está gravado

    asyncio.run(run())
    with integration_database.sessions() as session:
        reason = session.scalar(text("SELECT reason FROM identity_ai_breaker"))
    assert reason == "quota"


def test_breaker_read_error_never_aborts_the_callers_transaction(
    integration_database,
) -> None:
    from app.products.identity_ai_failure import breaker_open_until

    with integration_database.sessions.begin() as session:
        session.execute(text("DROP TABLE identity_ai_breaker"))
    _close_breaker_memory = AI_BREAKER.reset
    _close_breaker_memory()

    async def run():
        async with integration_database.async_sessions() as session:
            until = await breaker_open_until(session, datetime.now(UTC))
            # a transação do chamador continua utilizável depois do erro de leitura
            alive = await session.scalar(text("SELECT 1"))
            return until, alive

    until, alive = asyncio.run(run())
    assert until is None and alive == 1


def test_parallel_titles_spend_at_most_two_ai_calls_when_the_ai_is_down(
    integration_database,
) -> None:
    class _SlowDown:
        def __init__(self) -> None:
            self.calls = 0

        async def generate(self, request):
            self.calls += 1
            await asyncio.sleep(0.3)
            raise AIProviderQuotaExceeded()

    ai = _SlowDown()
    titles = [f"Memória sem marca conhecida modelo {n} 8GB DDR5" for n in range(8)]

    async def one(title):
        async with integration_database.async_sessions() as session:
            await resolve_or_learn_product_variant(
                session, raw_title=title, ai_manager=ai, profile=UserRole.ADMIN
            )
            await session.commit()

    async def run():
        await asyncio.gather(*(one(title) for title in titles))

    asyncio.run(run())
    assert 1 <= ai.calls <= 2, "o portão deixa gastar no máximo 2 chamadas"
    with integration_database.sessions() as session:
        total = session.scalar(
            select(func.count()).select_from(ProductIdentityCandidate)
        )
    assert total == len(titles), "todo título ganhou candidato, mesmo os pulados"
