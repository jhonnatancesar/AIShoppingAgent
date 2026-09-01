"""Invariantes do domínio compartilhado de feedback (subtask 7, validação
final) -- testadas direto em `app.feedback.service`, a única fronteira de
confiança real (Web e Telegram são só tradutores de payload/fluxo)."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from app.feedback.models import FeedbackChannel, FeedbackKind
from app.feedback.service import (
    FeedbackValidationError,
    create_feedback_async,
    validate_feedback_message,
    validate_store_name,
    validate_store_url,
)


def _session() -> MagicMock:
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


@pytest.mark.anyio
async def test_bug_requires_non_blank_message() -> None:
    with pytest.raises(FeedbackValidationError):
        await create_feedback_async(
            _session(),
            user_id=None,
            kind=FeedbackKind.BUG,
            channel=FeedbackChannel.WEB,
            message="   ",
        )


@pytest.mark.anyio
async def test_support_requires_non_blank_message() -> None:
    with pytest.raises(FeedbackValidationError):
        await create_feedback_async(
            _session(),
            user_id=None,
            kind=FeedbackKind.SUPPORT,
            channel=FeedbackChannel.TELEGRAM,
            message=None,
        )


@pytest.mark.anyio
async def test_store_suggestion_requires_non_blank_store_name() -> None:
    with pytest.raises(FeedbackValidationError):
        await create_feedback_async(
            _session(),
            user_id=None,
            kind=FeedbackKind.STORE_SUGGESTION,
            channel=FeedbackChannel.WEB,
            message=None,
            store_name="   ",
        )


@pytest.mark.anyio
async def test_store_suggestion_allows_missing_comment() -> None:
    session = _session()
    feedback = await create_feedback_async(
        session,
        user_id=None,
        kind=FeedbackKind.STORE_SUGGESTION,
        channel=FeedbackChannel.TELEGRAM,
        message=None,
        store_name="Loja Nova",
    )
    assert feedback.message is None
    assert feedback.store_name == "Loja Nova"


@pytest.mark.anyio
async def test_message_over_limit_is_rejected() -> None:
    with pytest.raises(FeedbackValidationError):
        await create_feedback_async(
            _session(),
            user_id=None,
            kind=FeedbackKind.BUG,
            channel=FeedbackChannel.WEB,
            message="x" * 4001,
        )


@pytest.mark.anyio
async def test_store_name_over_limit_is_rejected() -> None:
    with pytest.raises(FeedbackValidationError):
        await create_feedback_async(
            _session(),
            user_id=None,
            kind=FeedbackKind.STORE_SUGGESTION,
            channel=FeedbackChannel.WEB,
            message=None,
            store_name="x" * 161,
        )


@pytest.mark.anyio
async def test_store_url_scheme_is_enforced_even_when_message_is_valid() -> None:
    with pytest.raises(FeedbackValidationError):
        await create_feedback_async(
            _session(),
            user_id=None,
            kind=FeedbackKind.STORE_SUGGESTION,
            channel=FeedbackChannel.TELEGRAM,
            message="comentário válido",
            store_name="Loja Nova",
            store_url="javascript:alert(1)",
        )


def test_validate_store_url_rejects_javascript_scheme() -> None:
    with pytest.raises(FeedbackValidationError):
        validate_store_url("javascript:alert(1)")


def test_validate_store_url_rejects_data_scheme() -> None:
    with pytest.raises(FeedbackValidationError):
        validate_store_url("data:text/html,<script>alert(1)</script>")


def test_validate_store_url_rejects_missing_scheme() -> None:
    with pytest.raises(FeedbackValidationError):
        validate_store_url("lojanova.example.com")


def test_validate_store_url_accepts_http_and_https() -> None:
    validate_store_url("https://lojanova.example.com")
    validate_store_url("http://lojanova.example.com")


def test_validate_store_url_rejects_over_limit() -> None:
    with pytest.raises(FeedbackValidationError):
        validate_store_url("https://example.com/" + "a" * 2048)


def test_validate_feedback_message_rejects_over_limit() -> None:
    with pytest.raises(FeedbackValidationError):
        validate_feedback_message("x" * 4001)


def test_validate_store_name_rejects_over_limit() -> None:
    with pytest.raises(FeedbackValidationError):
        validate_store_name("x" * 161)
