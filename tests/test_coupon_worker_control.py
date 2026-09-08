"""`app.coupons.worker_control` -- aviso best-effort ao Coupon Worker
quando o GG detecta HIGH_ACTIVITY (2026-09-08). Infraestrutura pronta,
ainda não fiada no scheduler de produção (ver relatório) -- estes testes
cobrem o módulo isoladamente: liga só quando configurado, nunca levanta
exceção, nunca imprime/loga o token."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from app.core.config import Settings
from app.coupons.worker_control import notify_coupon_worker_high_activity

NOW = datetime(2026, 9, 8, 3, 0, tzinfo=UTC)


def _client_factory(response: httpx.Response | None = None, *, raises: Exception | None = None):
    post = AsyncMock()
    if raises is not None:
        post.side_effect = raises
    else:
        post.return_value = response

    client = MagicMock()
    client.post = post
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    def factory(*args, **kwargs):
        return client

    return factory, post


@pytest.mark.anyio
async def test_does_nothing_when_url_not_configured(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("abc", encoding="utf-8")
    settings = Settings(
        coupon_worker_control_url=None, coupon_worker_control_token_file=token_file
    )
    factory, post = _client_factory()

    await notify_coupon_worker_high_activity(settings, now=NOW, client_factory=factory)

    post.assert_not_called()


@pytest.mark.anyio
async def test_does_nothing_when_token_file_missing(tmp_path: Path) -> None:
    settings = Settings(
        coupon_worker_control_url="http://127.0.0.1:8090",
        coupon_worker_control_token_file=tmp_path / "does-not-exist",
    )
    factory, post = _client_factory()

    await notify_coupon_worker_high_activity(settings, now=NOW, client_factory=factory)

    post.assert_not_called()


@pytest.mark.anyio
async def test_posts_promo_window_with_correct_payload_and_auth(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("secret-token-value\n", encoding="utf-8")
    settings = Settings(
        coupon_worker_control_url="http://127.0.0.1:8090/",
        coupon_worker_control_token_file=token_file,
        collection_high_activity_duration_minutes=45,
    )
    factory, post = _client_factory(httpx.Response(200, json={"ok": True}))

    await notify_coupon_worker_high_activity(settings, now=NOW, client_factory=factory)

    post.assert_awaited_once()
    args, kwargs = post.call_args
    assert args[0] == "http://127.0.0.1:8090/control/promo"
    assert kwargs["headers"]["Authorization"] == "Bearer secret-token-value"
    assert kwargs["json"]["mode"] == "promotion"
    assert kwargs["json"]["window_start"] == NOW.isoformat()
    assert kwargs["json"]["window_end"] == "2026-09-08T03:45:00+00:00"


@pytest.mark.anyio
async def test_never_raises_when_worker_unreachable(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("abc", encoding="utf-8")
    settings = Settings(
        coupon_worker_control_url="http://127.0.0.1:8090",
        coupon_worker_control_token_file=token_file,
    )
    factory, post = _client_factory(raises=httpx.ConnectError("recusado"))

    await notify_coupon_worker_high_activity(settings, now=NOW, client_factory=factory)  # não levanta

    post.assert_awaited_once()


@pytest.mark.anyio
async def test_never_raises_on_non_200_response(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("abc", encoding="utf-8")
    settings = Settings(
        coupon_worker_control_url="http://127.0.0.1:8090",
        coupon_worker_control_token_file=token_file,
    )
    factory, post = _client_factory(httpx.Response(401, json={"error": "unauthorized"}))

    await notify_coupon_worker_high_activity(settings, now=NOW, client_factory=factory)  # não levanta

    post.assert_awaited_once()
