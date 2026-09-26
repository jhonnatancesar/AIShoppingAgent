"""TASK-128 etapa 2 -- "não entendi" vira leitura da página do produto
pelo worker, contra Postgres real com a migration `20260926_0002`.
Decisão do usuário (2026-09-26): "retornar ao GG que não entendeu e
abrir o worker e ler a página e passar mais detalhes para a IA".

Prova: o "não entendi" guarda o Product de origem; a varredura abre a
página de uma Offer dele, a IA tenta de novo com título + página e o
resultado SUBSTITUI o `awaiting_page` (parcial, exato ou terminal
`unrecognized`), vinculando o Product; loja sem página de produto,
falhas repetidas e reserva de tempo nunca viram laço de IA/navegação."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.ai_provider import AIResponse
from app.collection.contracts import ProductPageRead, ProductPageReadStatus
from app.offers.models import Offer
from app.products.identity_candidates import ProductIdentityCandidate
from app.products.identity_learning import resolve_or_learn_product_variant
from app.products.identity_page import resolve_awaiting_page_titles
from app.products.models import Product
from app.stores.models import Store
from app.users.models import UserRole
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
_PAGE_MARKER = "Dados da página do produto:"


def _extraction(*, category="", brand="", family="", model="") -> dict:
    return {
        "category": category,
        "brand": brand,
        "family": family,
        "model": model,
        "variant": None,
        "store_sku": None,
        "manufacturer_part_number": None,
        "attributes": {},
    }


class _TitleThenPageAIManager:
    """Uma resposta para o título sozinho ("não entendi") e outra quando
    a mensagem traz os dados da página -- conta as chamadas de cada."""

    def __init__(self, *, with_page: dict | None) -> None:
        self._with_page = with_page
        self.title_calls = 0
        self.page_calls = 0
        self.page_messages: list[str] = []

    async def generate(self, request):
        message = request.messages[-1].content
        if _PAGE_MARKER in message:
            self.page_calls += 1
            self.page_messages.append(message)
            content = json.dumps(self._with_page or _extraction())
        else:
            self.title_calls += 1
            content = json.dumps(_extraction())
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub-identity-model",
            content=content,
            finished_at=datetime.now(UTC),
        )


class _PageReader:
    def __init__(self, *results: ProductPageRead) -> None:
        self._results = list(results)
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, store_code: str, url: str) -> ProductPageRead:
        self.calls.append((store_code, url))
        return self._results.pop(0) if len(self._results) > 1 else self._results[0]


def _read(context: str) -> ProductPageRead:
    return ProductPageRead(ProductPageReadStatus.READ, context)


def _seed_not_understood(integration_database, manager, title, *, with_offer=True):
    """Product ad-hoc + Offer na Amazon; a primeira resolução (só título)
    grava o "não entendi" (`awaiting_page`) com o Product de origem."""
    url = f"https://example.invalid/page-read/{uuid4()}"
    with integration_database.sessions.begin() as session:
        store_id = session.scalar(select(Store.id).where(Store.code == "amazon"))
        product = Product(id=uuid4(), name=title)
        session.add(product)
        session.flush()
        if with_offer:
            session.add(
                Offer(
                    product_id=product.id,
                    store_id=store_id,
                    external_id=f"page-read-{uuid4()}",
                    url=url,
                )
            )
        product_id = product.id

    async def _first_resolution():
        async with integration_database.async_sessions() as session:
            resolved = await resolve_or_learn_product_variant(
                session,
                raw_title=title,
                ai_manager=manager,
                profile=UserRole.ADMIN,
                source_product_id=product_id,
            )
            await session.commit()
            return resolved

    assert asyncio.run(_first_resolution()) is None
    return product_id, url


def _sweep(integration_database, reader, manager, **kwargs):
    return asyncio.run(
        resolve_awaiting_page_titles(
            integration_database.async_sessions,
            page_reader=reader,
            ai_manager=manager,
            profile=UserRole.ADMIN,
            **kwargs,
        )
    )


def _candidate(integration_database) -> ProductIdentityCandidate:
    with integration_database.sessions() as session:
        [candidate] = list(session.scalars(select(ProductIdentityCandidate)))
        return candidate


_CHAIR = "Cadeira Reclinável Preta com Almofada"
_HEADSET = "Headset Gamer Preto P2 com Microfone"


def test_not_understood_title_records_its_source_product(
    integration_database,
) -> None:
    manager = _TitleThenPageAIManager(with_page=None)
    product_id, _url = _seed_not_understood(integration_database, manager, _CHAIR)

    candidate = _candidate(integration_database)
    assert candidate.status == "awaiting_page"
    assert candidate.source_product_id == product_id
    assert (candidate.page_attempts, candidate.page_read_at) == (0, None)


def test_page_data_turns_not_understood_into_a_partial_link(
    integration_database,
) -> None:
    manager = _TitleThenPageAIManager(
        with_page=_extraction(category="Cadeira Gamer", brand="ThunderX3")
    )
    product_id, url = _seed_not_understood(integration_database, manager, _CHAIR)
    page = "Marca: ThunderX3\nTrilha de categorias: Games > Cadeiras Gamer"
    reader = _PageReader(_read(page))

    summary = _sweep(integration_database, reader, manager, now=NOW)

    assert (summary.claimed, summary.linked) == (1, 1)
    assert reader.calls == [("amazon", url)]
    assert manager.page_messages == [
        f"Título do anúncio: {_CHAIR}\n\n{_PAGE_MARKER}\n{page}"
    ]
    candidate = _candidate(integration_database)
    assert candidate.status == "partial"
    assert (candidate.category, candidate.brand) == ("cadeira-gamer", "thunderx3")
    assert candidate.page_context == page
    with integration_database.sessions() as session:
        product = session.get(Product, product_id)
        assert (product.category, product.brand) == ("cadeira-gamer", "thunderx3")
        assert product.identity_key is None


def test_page_data_can_close_an_exact_identity_and_promote_the_product(
    integration_database,
) -> None:
    manager = _TitleThenPageAIManager(
        with_page=_extraction(
            category="headset", brand="HyperX", family="Cloud", model="Stinger 2"
        )
    )
    product_id, _url = _seed_not_understood(integration_database, manager, _HEADSET)
    reader = _PageReader(_read("Nome: Headset HyperX Cloud Stinger 2\nMarca: HyperX"))

    summary = _sweep(integration_database, reader, manager, now=NOW)

    assert summary.linked == 1
    candidate = _candidate(integration_database)
    assert candidate.status == "approved"
    assert candidate.grounded is True
    with integration_database.sessions() as session:
        product = session.get(Product, product_id)
        assert product.identity_key == candidate.identity_key
        assert (product.brand, product.model) == ("hyperx", "stinger-2")

    # O MESMO título (outra loja/coleta) agora sai do cache, sem IA.
    async def _again():
        async with integration_database.async_sessions() as session:
            return await resolve_or_learn_product_variant(
                session, raw_title=_HEADSET, ai_manager=manager
            )

    assert asyncio.run(_again()).identity_key == candidate.identity_key
    assert (manager.title_calls, manager.page_calls) == (1, 1)


def test_store_without_product_pages_is_terminal_without_any_ai_call(
    integration_database,
) -> None:
    manager = _TitleThenPageAIManager(with_page=_extraction(category="x"))
    product_id, _url = _seed_not_understood(integration_database, manager, _CHAIR)
    reader = _PageReader(ProductPageRead(ProductPageReadStatus.UNSUPPORTED))

    summary = _sweep(integration_database, reader, manager, now=NOW)

    assert (summary.claimed, summary.unrecognized) == (1, 1)
    assert manager.page_calls == 0
    assert _candidate(integration_database).status == "unrecognized"
    with integration_database.sessions() as session:
        assert session.get(Product, product_id).category is None


def test_page_that_still_does_not_help_is_terminal(integration_database) -> None:
    manager = _TitleThenPageAIManager(with_page=None)
    _seed_not_understood(integration_database, manager, _CHAIR)
    reader = _PageReader(_read("Título da página: Oferta Relâmpago"))

    summary = _sweep(integration_database, reader, manager, now=NOW)
    later = _sweep(integration_database, reader, manager, now=NOW + timedelta(days=1))

    assert summary.unrecognized == 1
    assert later.claimed == 0, "terminal nunca volta para a fila"
    candidate = _candidate(integration_database)
    assert candidate.status == "unrecognized"
    assert candidate.page_context == "Título da página: Oferta Relâmpago"
    assert (manager.title_calls, manager.page_calls) == (1, 1)


def test_transient_failures_retry_after_the_lease_then_give_up(
    integration_database,
) -> None:
    manager = _TitleThenPageAIManager(with_page=_extraction(category="x"))
    _seed_not_understood(integration_database, manager, _CHAIR)
    reader = _PageReader(ProductPageRead(ProductPageReadStatus.FAILED))

    first = _sweep(integration_database, reader, manager, now=NOW)
    during_lease = _sweep(
        integration_database, reader, manager, now=NOW + timedelta(minutes=5)
    )
    assert (first.retry_later, during_lease.claimed) == (1, 0)

    moment = NOW
    for _ in range(2):
        moment += timedelta(minutes=31)
        _sweep(integration_database, reader, manager, now=moment)
    after_limit = _sweep(
        integration_database, reader, manager, now=moment + timedelta(days=1)
    )

    assert len(reader.calls) == 3
    assert after_limit.claimed == 0
    candidate = _candidate(integration_database)
    assert (candidate.status, candidate.page_attempts) == ("unrecognized", 3)
    assert manager.page_calls == 0


def test_missing_offer_is_retried_then_given_up_without_navigation(
    integration_database,
) -> None:
    manager = _TitleThenPageAIManager(with_page=_extraction(category="x"))
    _seed_not_understood(integration_database, manager, _CHAIR, with_offer=False)
    reader = _PageReader(_read("Marca: X"))

    summary = _sweep(integration_database, reader, manager, now=NOW, max_attempts=1)

    assert summary.unrecognized == 1
    assert reader.calls == []


def test_attempt_interrupted_midway_is_closed_on_the_next_sweep(
    integration_database,
) -> None:
    """Se o processo caiu depois da última tentativa reservada, a próxima
    varredura encerra o título (terminal) em vez de deixá-lo órfão."""
    manager = _TitleThenPageAIManager(with_page=None)
    _seed_not_understood(integration_database, manager, _CHAIR)
    with integration_database.sessions.begin() as session:
        session.execute(
            text(
                "UPDATE product_identity_candidates "
                "SET page_attempts = 3, page_read_at = :at"
            ),
            {"at": NOW - timedelta(hours=1)},
        )

    summary = _sweep(integration_database, _PageReader(_read("x")), manager, now=NOW)

    assert summary.claimed == 0
    assert _candidate(integration_database).status == "unrecognized"


def test_database_rejects_unrecognized_candidate_with_category(
    integration_database,
) -> None:
    with (
        pytest.raises(IntegrityError),
        integration_database.sessions.begin() as session,
    ):
        session.execute(
            text(
                "INSERT INTO product_identity_candidates "
                "(id, raw_title, normalized_title_hash, status, grounded, "
                "created_at, category) "
                "VALUES (:id, 'x', :hash, 'unrecognized', false, now(), 'cpu')"
            ),
            {"id": uuid4(), "hash": uuid4().hex},
        )
