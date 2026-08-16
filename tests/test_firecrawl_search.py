"""Contrato Firecrawl Search v2 sem tráfego externo."""

import pytest
from app.search.firecrawl import (
    FirecrawlSearchError,
    FirecrawlSearchProvider,
    parse_firecrawl_search_response,
)
from pydantic import SecretStr


class _Response:
    status_code = 200

    def __init__(self, body: object) -> None:
        self._body = body

    def json(self) -> object:
        return self._body


class _Client:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, _url: str, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _payload() -> dict[str, object]:
    return {
        "success": True,
        "data": {
            "web": [
                {
                    "title": "Resultado de teste",
                    "description": "Descrição",
                    "url": "https://example.com",
                }
            ]
        },
        "creditsUsed": 1,
    }


def test_parser_preserves_firecrawl_v2_web_result_and_credits() -> None:
    response = parse_firecrawl_search_response(_payload())

    assert response.success is True
    assert response.search_performed is True
    assert len(response.results) == 1
    assert response.results[0].title == "Resultado de teste"
    assert response.results[0].description == "Descrição"
    assert response.results[0].url == "https://example.com"
    assert response.credits_used == 1


def test_empty_web_results_are_not_considered_grounding() -> None:
    response = parse_firecrawl_search_response(
        {"success": True, "data": {"web": []}, "creditsUsed": 0}
    )

    assert response.search_performed is False


@pytest.mark.anyio
async def test_client_sends_v2_web_source_and_limit_without_leaking_key() -> None:
    transport = _Client(_Response(_payload()))
    client = FirecrawlSearchProvider(
        SecretStr("secret-canary"), client_factory=lambda **_kwargs: transport
    )

    response = await client.search("consulta atual", sources=("web",), limit=2)

    assert response.search_performed is True
    assert transport.calls[0]["json"] == {
        "query": "consulta atual",
        "sources": ["web"],
        "limit": 2,
    }
    assert "secret-canary" not in repr(response)


def test_v1_list_shape_is_rejected() -> None:
    with pytest.raises(FirecrawlSearchError, match="firecrawl_response_invalid"):
        parse_firecrawl_search_response(
            {"success": True, "data": [{"title": "v1", "url": "https://x"}]}
        )
