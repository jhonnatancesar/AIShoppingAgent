"""Testes de normalização de título e classificação de relevância (TASK-063)."""

import json
from datetime import UTC, datetime

import pytest
from app.ai_provider import AIProviderError, AIRequest, AIResponse
from app.collection.relevance import (
    RELEVANCE_CLASSIFICATION_PURPOSE,
    TITLE_NORMALIZATION_PURPOSE,
    OfferRelevance,
    classify_offer_relevance,
    normalize_offer_title,
)
from app.users.models import UserRole


class _FakeManager:
    """Substitui o AIProviderManager sem tocar em nenhum provedor real."""

    def __init__(self, content: str | None = None, *, raises: bool = False) -> None:
        self.content = content
        self.raises = raises
        self.captured_request: AIRequest | None = None

    async def generate(self, request: AIRequest) -> AIResponse:
        self.captured_request = request
        if self.raises:
            raise AIProviderError("fake_failure", retryable=False)
        return AIResponse(
            request_id=request.request_id,
            provider="fake_provider",
            model="fake-model",
            content=self.content or "{}",
            finished_at=datetime.now(UTC),
        )


@pytest.mark.anyio
async def test_normalize_offer_title_builds_request_with_correct_purpose_and_profile() -> (
    None
):
    manager = _FakeManager(json.dumps({"display_title": "Logitech G Pro X"}))

    title = await normalize_offer_title(
        manager, raw_title="Mouse Gamer Logitech G PRO X ...", profile=UserRole.ADMIN
    )

    assert title == "Logitech G Pro X"
    assert manager.captured_request.purpose == TITLE_NORMALIZATION_PURPOSE
    assert manager.captured_request.profile is UserRole.ADMIN


@pytest.mark.parametrize(
    "content",
    [
        None,  # provider indisponível
        "not json",
        "{}",
        json.dumps({"display_title": ""}),
        json.dumps({"display_title": 123}),
        json.dumps({"display_title": "ok", "extra": "field"}),
    ],
)
@pytest.mark.anyio
async def test_normalize_offer_title_is_none_on_any_invalid_response(
    content: str | None,
) -> None:
    manager = _FakeManager(content, raises=content is None)

    title = await normalize_offer_title(
        manager, raw_title="qualquer título", profile=UserRole.ADMIN
    )

    assert title is None


@pytest.mark.anyio
async def test_normalize_offer_title_never_invents_beyond_response_and_is_bounded() -> (
    None
):
    long_title = "X" * 500
    manager = _FakeManager(json.dumps({"display_title": long_title}))

    title = await normalize_offer_title(
        manager, raw_title="raw", profile=UserRole.ADMIN
    )

    assert title == long_title[:300]


@pytest.mark.anyio
async def test_classify_offer_relevance_builds_request_with_correct_purpose() -> None:
    manager = _FakeManager(json.dumps({"relevance": "match"}))

    classification = await classify_offer_relevance(
        manager,
        mission_search_query="rtx 4060",
        raw_title="Placa de vídeo RTX 4060 8GB",
        profile=UserRole.ADMIN,
    )

    assert classification is OfferRelevance.MATCH
    assert manager.captured_request.purpose == RELEVANCE_CLASSIFICATION_PURPOSE
    assert manager.captured_request.profile is UserRole.ADMIN
    sent = json.loads(manager.captured_request.messages[-1].content)
    assert sent == {
        "search_query": "rtx 4060",
        "listing_title": "Placa de vídeo RTX 4060 8GB",
    }


@pytest.mark.parametrize(
    "value",
    ["match", "possible_match", "no_match"],
)
@pytest.mark.anyio
async def test_classify_offer_relevance_parses_the_closed_vocabulary(
    value: str,
) -> None:
    manager = _FakeManager(json.dumps({"relevance": value}))

    classification = await classify_offer_relevance(
        manager, mission_search_query="q", raw_title="t", profile=UserRole.ADMIN
    )

    assert classification is OfferRelevance(value)


@pytest.mark.parametrize(
    "content",
    [
        None,  # provider indisponível
        "not json",
        "{}",
        json.dumps({"relevance": "maybe"}),  # fora do vocabulário fechado
        json.dumps({"relevance": "match", "extra": "field"}),
    ],
)
@pytest.mark.anyio
async def test_classify_offer_relevance_is_none_on_any_invalid_response(
    content: str | None,
) -> None:
    manager = _FakeManager(content, raises=content is None)

    classification = await classify_offer_relevance(
        manager, mission_search_query="q", raw_title="t", profile=UserRole.ADMIN
    )

    assert classification is None
