"""Contrato Firecrawl Search v2 sem tráfego externo."""

import pytest
from app.search.firecrawl import (
    FirecrawlSearchError,
    FirecrawlSearchProvider,
    parse_firecrawl_scrape_response,
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


# --- /v2/scrape (TASK-113 §33.18) -- contrato confirmado contra a
# documentação oficial (docs.firecrawl.dev, auditoria 2026-08-27):
# `data.markdown`/`data.metadata.{title,url,sourceURL,statusCode,error}`,
# nunca um `data.url` de nível superior.


def test_scrape_parser_reads_markdown_and_metadata_url() -> None:
    result = parse_firecrawl_scrape_response(
        {
            "success": True,
            "data": {
                "markdown": "Preço bom",
                "metadata": {
                    "title": "Produto X",
                    "url": "https://loja.example/produto",
                    "sourceURL": "https://loja.example/produto?utm=1",
                    "statusCode": 200,
                },
            },
        }
    )

    assert result is not None
    assert result.markdown == "Preço bom"
    assert result.title == "Produto X"
    assert result.url == "https://loja.example/produto"  # metadata.url, nunca data.url


def test_scrape_parser_falls_back_to_source_url_when_final_url_absent() -> None:
    result = parse_firecrawl_scrape_response(
        {
            "success": True,
            "data": {
                "markdown": "Preço bom",
                "metadata": {"sourceURL": "https://loja.example/produto"},
            },
        }
    )

    assert result is not None
    assert result.url == "https://loja.example/produto"


def test_scrape_parser_rejects_origin_blocked_status_code() -> None:
    """`success: true` no envelope, mas a ORIGEM específica bloqueou --
    nunca tratado como evidência válida (correção pós-auditoria)."""
    result = parse_firecrawl_scrape_response(
        {
            "success": True,
            "data": {
                "markdown": "",
                "metadata": {"statusCode": 403, "url": "https://loja.example/bloqueado"},
            },
        }
    )

    assert result is None


def test_scrape_parser_rejects_metadata_error() -> None:
    result = parse_firecrawl_scrape_response(
        {
            "success": True,
            "data": {
                "markdown": "",
                "metadata": {"error": "timeout scraping page", "statusCode": 200},
            },
        }
    )

    assert result is None


def test_scrape_parser_rejects_service_level_failure() -> None:
    assert parse_firecrawl_scrape_response({"success": False}) is None
