"""Contract opt-in: exige Core DEV real já iniciado e OmniRoute real configurado."""

import asyncio
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.ai_provider.contracts import AIMessage, AIMessageRole, AIRequest
from app.ai_provider.manager import build_admin_dev_ai_provider_manager
from app.core.config import Settings
from app.users.models import UserRole


@pytest.mark.skipif(
    os.environ.get("AISHOPPING_RUN_CESAR_CORE_CONTRACTS") != "1",
    reason="Requires explicitly enabled live Core DEV contract environment",
)
def test_real_gg_oferta_manager_core_omniroute(caplog):
    settings = Settings(_env_file=None)
    assert settings.environment != "production"
    assert settings.cesar_core_api_key_file is not None
    request = AIRequest(
        uuid4(),
        UserRole.DEV,
        "typed_roles_validation",
        (
            AIMessage(
                AIMessageRole.SYSTEM, "Responda exatamente com a palavra CAPPED."
            ),
            AIMessage(AIMessageRole.USER, "Qual palavra você deve responder?"),
        ),
        datetime.now(UTC),
    )
    result = asyncio.run(
        build_admin_dev_ai_provider_manager(settings).generate(request)
    )
    assert result.request_id == request.request_id
    assert result.provider == "cesar_core"
    assert result.model and result.content.strip() == "CAPPED"
    assert settings.cesar_core_api_key_file.read_text().strip() not in caplog.text
    assert all(message.content not in caplog.text for message in request.messages)


@pytest.mark.skipif(
    os.environ.get("AISHOPPING_RUN_CESAR_CORE_CONTRACTS") != "1",
    reason="Requires explicitly enabled live Core DEV contract environment",
)
def test_real_grounding_uses_core_omniroute_web_search(caplog):
    settings = Settings(_env_file=None)
    request = AIRequest(
        uuid4(),
        UserRole.DEV,
        "grounding_validation",
        (
            AIMessage(
                AIMessageRole.SYSTEM,
                "Use Web search and answer briefly using the retrieved evidence.",
            ),
            AIMessage(
                AIMessageRole.USER,
                "What is the official Python documentation website?",
            ),
        ),
        datetime.now(UTC),
        require_search_grounding=True,
    )
    result = asyncio.run(
        build_admin_dev_ai_provider_manager(settings).generate(request)
    )
    assert result.provider == "cesar_core"
    assert result.content.strip()
    assert result.grounding_requested and result.grounding_performed
    assert "https://docs.python.org/" in result.grounding_sources
    assert settings.cesar_core_api_key_file is not None
    assert settings.cesar_core_api_key_file.read_text().strip() not in caplog.text
    assert all(message.content not in caplog.text for message in request.messages)
