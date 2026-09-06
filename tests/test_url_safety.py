"""Regressão de segurança FASE E.3: vazamento de dados na URL de
enriquecimento (`app/search/url_safety.py`).

Cobre as duas decisões distintas do módulo -- SE uma URL deve ser buscada
(``is_safe_to_fetch``) e QUAL representação dela é segura persistir/propagar
(``safe_url_for_evidence``, ``url_for_fetch_request``, ``redact_sensitive_
query_values``) -- e é deliberadamente independente do guard de SSRF do
César Core (`DEC-111`): isto aqui nunca decide PRA ONDE a URL aponta na
rede. Usa apenas hosts sintéticos e segredos claramente falsos
(``FAKE_AUDIT_TOKEN_123`` e afins), nunca um valor real.
"""

import pytest
from app.search.url_safety import (
    has_sensitive_query_data,
    is_safe_to_fetch,
    redact_sensitive_query_values,
    safe_url_for_evidence,
    url_for_fetch_request,
)

# --- has_sensitive_query_data / is_safe_to_fetch ---------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://shop.example.test/product?access_token=FAKE_AUDIT_TOKEN_123",
        "https://shop.example.test/product?refresh_token=FAKE_AUDIT_TOKEN_123",
        "https://shop.example.test/product?oauth_token=FAKE_AUDIT_TOKEN_123",
        "https://shop.example.test/product?api_key=FAKE_AUDIT_TOKEN_123",
        "https://shop.example.test/product?apikey=FAKE_AUDIT_TOKEN_123",
        "https://shop.example.test/product?api-key=FAKE_AUDIT_TOKEN_123",
        "https://shop.example.test/product?Authorization=FAKE_AUDIT_TOKEN_123",
        "https://shop.example.test/product?password=FAKE_AUDIT_TOKEN_123",
        "https://shop.example.test/product?passwd=FAKE_AUDIT_TOKEN_123",
        "https://shop.example.test/product?session=FAKE_AUDIT_TOKEN_123",
        "https://shop.example.test/product?session_id=FAKE_AUDIT_TOKEN_123",
        "https://shop.example.test/product?jwt=FAKE_AUDIT_TOKEN_123",
        "https://cdn.example.test/img.jpg?X-Amz-Signature=FAKE_SIG&X-Amz-Credential=FAKE_CRED",
        "https://storage.googleapis.com/bucket/obj?X-Goog-Signature=FAKE_SIG",
    ],
)
def test_detects_high_confidence_sensitive_urls(url: str) -> None:
    assert has_sensitive_query_data(url) is True
    assert is_safe_to_fetch(url) is False


def test_detects_azure_sas_by_sig_and_sv_combination() -> None:
    url = "https://acct.blob.core.windows.net/c/blob?sv=2024-01-01&sig=FAKE_SAS"
    assert has_sensitive_query_data(url) is True
    assert is_safe_to_fetch(url) is False


def test_does_not_flag_lone_sig_or_sv_without_the_other() -> None:
    assert has_sensitive_query_data("https://shop.example.test/p?sig=abc123") is False
    assert (
        has_sensitive_query_data("https://shop.example.test/p?sv=2024-01-01") is False
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://shop.example.test/product?id=123",
        "https://shop.example.test/product?sku=ABC-1",
        "https://shop.example.test/product?product=notebook",
        "https://shop.example.test/product?ref=homepage",
        "https://shop.example.test/product?page=2",
        "https://shop.example.test/product?category=notebooks",
        "https://shop.example.test/product?code=PROMO10",
        "https://shop.example.test/product?key=featured",
        "https://shop.example.test/product",
    ],
)
def test_common_ecommerce_parameters_are_never_flagged(url: str) -> None:
    assert has_sensitive_query_data(url) is False
    assert is_safe_to_fetch(url) is True


# --- safe_url_for_evidence ---------------------------------------------------


def test_evidence_url_drops_fragment() -> None:
    result = safe_url_for_evidence("https://shop.example.test/product?id=1#reviews")
    assert result == "https://shop.example.test/product?id=1"


def test_evidence_url_removes_sensitive_keys_but_keeps_legitimate_ones() -> None:
    url = "https://shop.example.test/product?id=1&session_id=FAKE_AUDIT_TOKEN_123&ref=home"
    result = safe_url_for_evidence(url)
    assert "session_id" not in result
    assert "FAKE_AUDIT_TOKEN_123" not in result
    assert "id=1" in result
    assert "ref=home" in result


def test_evidence_url_removes_azure_sas_markers_together() -> None:
    url = "https://acct.blob.core.windows.net/c/blob?sv=2024-01-01&sig=FAKE_SAS&id=1"
    result = safe_url_for_evidence(url)
    assert "sv=" not in result
    assert "sig=" not in result
    assert "id=1" in result


def test_evidence_url_is_unchanged_for_an_already_safe_url() -> None:
    url = "https://shop.example.test/product?id=1&ref=home"
    assert safe_url_for_evidence(url) == url


# --- url_for_fetch_request ---------------------------------------------------


def test_fetch_request_url_strips_fragment_only() -> None:
    url = "https://shop.example.test/product?id=1#reviews"
    assert url_for_fetch_request(url) == "https://shop.example.test/product?id=1"


def test_fetch_request_url_leaves_query_untouched_when_no_fragment() -> None:
    url = "https://shop.example.test/product?id=1&ref=home"
    assert url_for_fetch_request(url) == url


def test_fetch_request_url_strips_a_fragment_that_itself_carries_a_secret() -> None:
    url = "https://shop.example.test/callback#access_token=FAKE_AUDIT_TOKEN_123"
    assert url_for_fetch_request(url) == "https://shop.example.test/callback"


# --- redact_sensitive_query_values -------------------------------------------


def test_redacts_sensitive_key_value_pairs_in_free_text() -> None:
    text = (
        "Falha ao buscar "
        "https://shop.example.test/product?session_id=FAKE_AUDIT_TOKEN_123: timeout"
    )
    result = redact_sensitive_query_values(text)
    assert result.redacted is True
    assert "FAKE_AUDIT_TOKEN_123" not in result.text
    assert "session_id=[REDACTED]" in result.text


def test_redacts_multiple_occurrences_in_the_same_text() -> None:
    text = "api_key=FAKE_ONE&password=FAKE_TWO"
    result = redact_sensitive_query_values(text)
    assert result.redacted is True
    assert "FAKE_ONE" not in result.text
    assert "FAKE_TWO" not in result.text


def test_does_not_redact_text_without_sensitive_keys() -> None:
    text = "Falha ao buscar https://shop.example.test/product?id=1: timeout"
    result = redact_sensitive_query_values(text)
    assert result.redacted is False
    assert result.text == text
