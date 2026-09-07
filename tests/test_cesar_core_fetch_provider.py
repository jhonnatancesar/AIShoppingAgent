"""Contrato do enriquecimento de URL via César Core, sem tráfego externo."""

import asyncio
import json

import httpx
import pytest
from app.search.cesar_core_fetch import CesarCoreFetchError, CesarCoreFetchProvider


def provider(tmp_path, handler, *, key_text: str = "synthetic-fixture-only"):
    key = tmp_path / "key"
    key.write_text(key_text, encoding="utf-8")
    return CesarCoreFetchProvider(
        api_key_file=key,
        base_url="http://127.0.0.1:8100",
        service="backend",
        client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )


def _ok_body(**overrides) -> dict:
    body = {
        "request_id": "core-id",
        "correlation_id": None,  # preenchido pelo handler a partir do request real
        "provider_gateway": "omniroute",
        "provider": "firecrawl",
        "url": "https://example.test/product",
        "fetched": True,
        "title": "Produto",
        "content": "# Produto\nPreço R$ 100",
        "truncated": False,
        "usage": {"fetch_cost_usd": 0.0},
        "latency_ms": 12.3,
    }
    body.update(overrides)
    return body


def test_accepts_host_docker_internal_base_url(tmp_path):
    """DEC-121: o container `api` fala com o Core via `host.docker.internal`,
    nunca `127.0.0.1` (que dentro do container aponta para si mesmo)."""
    key = tmp_path / "key"
    key.write_text("synthetic-fixture-only", encoding="utf-8")
    provider_instance = CesarCoreFetchProvider(
        api_key_file=key,
        base_url="http://host.docker.internal:8100",
        service="backend",
    )
    assert provider_instance is not None


def test_rejects_arbitrary_hostname_base_url(tmp_path):
    key = tmp_path / "key"
    key.write_text("synthetic-fixture-only", encoding="utf-8")
    with pytest.raises(ValueError, match="loopback or host.docker.internal"):
        CesarCoreFetchProvider(
            api_key_file=key,
            base_url="http://cesar-core.example.com:8100",
            service="backend",
        )


def test_fetch_sends_neutral_payload_and_normalizes_response(tmp_path):
    wire = []

    def handler(req):
        payload = json.loads(req.content)
        wire.append(payload)
        assert req.headers["Authorization"] == "Bearer synthetic-fixture-only"
        assert req.headers["X-Service"] == "backend"
        assert req.headers["X-Purpose"] == "market_research"
        correlation_id = req.headers["X-Correlation-Id"]
        return httpx.Response(200, json=_ok_body(correlation_id=correlation_id))

    result = asyncio.run(
        provider(tmp_path, handler).scrape_basic("https://example.test/product")
    )
    assert wire == [
        {
            "url": "https://example.test/product",
            "requirements": {"service_class": "economy", "cost_policy": "free_only"},
        }
    ]
    assert result is not None
    assert result.url == "https://example.test/product"
    assert result.title == "Produto"
    assert result.markdown == "# Produto\nPreço R$ 100"
    # Nenhum dado provider-specific (Firecrawl) chega na payload enviada ao Core.
    assert "firecrawl" not in json.dumps(wire).lower()


def test_no_evidence_from_source_returns_none_not_an_error(tmp_path):
    def handler(req):
        body = _ok_body(
            correlation_id=req.headers["X-Correlation-Id"],
            fetched=False,
            content=None,
            title=None,
        )
        return httpx.Response(200, json=body)

    result = asyncio.run(
        provider(tmp_path, handler).scrape_basic("https://example.test/blocked")
    )
    assert result is None


def test_blank_content_despite_fetched_true_is_treated_as_no_evidence(tmp_path):
    def handler(req):
        body = _ok_body(correlation_id=req.headers["X-Correlation-Id"], content="   ")
        return httpx.Response(200, json=body)

    result = asyncio.run(
        provider(tmp_path, handler).scrape_basic("https://example.test/empty")
    )
    assert result is None


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "core_fetch_authentication_failed"),
        (403, "core_fetch_authentication_failed"),
        (429, "core_fetch_rate_limited"),
        (503, "core_fetch_request_failed"),
    ],
)
def test_http_error_statuses_are_normalized_without_raw_body(tmp_path, status, code):
    def handler(req):
        return httpx.Response(
            status, json={"error": {"message": "raw upstream detail"}}
        )

    with pytest.raises(CesarCoreFetchError) as error:
        asyncio.run(
            provider(tmp_path, handler).scrape_basic("https://example.test/product")
        )
    assert error.value.code == code
    assert error.value.status_code == status


def test_invalid_envelope_fails_closed(tmp_path):
    def handler(req):
        return httpx.Response(200, json={"fetched": True})

    with pytest.raises(CesarCoreFetchError) as error:
        asyncio.run(
            provider(tmp_path, handler).scrape_basic("https://example.test/product")
        )
    assert error.value.code == "core_fetch_invalid_response"


def test_missing_credential_file_fails_closed(tmp_path):
    missing = tmp_path / "does-not-exist"
    p = CesarCoreFetchProvider(
        api_key_file=missing, base_url="http://127.0.0.1:8100", service="backend"
    )
    with pytest.raises(CesarCoreFetchError) as error:
        asyncio.run(p.scrape_basic("https://example.test/product"))
    assert error.value.code == "core_fetch_credential_unavailable"


def test_blank_url_is_rejected_before_any_request(tmp_path):
    def handler(req):
        raise AssertionError("must not call Core for a blank URL")

    with pytest.raises(CesarCoreFetchError) as error:
        asyncio.run(provider(tmp_path, handler).scrape_basic("   "))
    assert error.value.code == "core_fetch_query_required"


def test_connection_failure_is_normalized(tmp_path):
    def handler(req):
        raise httpx.ConnectError("refused", request=req)

    with pytest.raises(CesarCoreFetchError) as error:
        asyncio.run(
            provider(tmp_path, handler).scrape_basic("https://example.test/product")
        )
    assert error.value.code == "core_fetch_network_unavailable"
