"""Limites HTTP aplicados antes da validação de payload pelo FastAPI."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

ASGIApp = Callable[
    [dict[str, Any], Callable[..., Awaitable[dict]], Callable[..., Awaitable[None]]],
    Awaitable[None],
]


class RequestBodyTooLarge(RuntimeError):
    pass


class MaxRequestBodyMiddleware:
    """Rejeita corpo acima do limite mesmo quando chega sem Content-Length."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", ())}
        raw_length = headers.get(b"content-length")
        if raw_length is not None:
            try:
                content_length = int(raw_length)
            except ValueError:
                content_length = self.max_bytes + 1
            if content_length > self.max_bytes:
                await _send_too_large(send)
                return

        consumed = 0

        async def limited_receive() -> dict:
            nonlocal consumed
            message = await receive()
            if message.get("type") == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > self.max_bytes:
                    raise RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestBodyTooLarge:
            await _send_too_large(send)


async def _send_too_large(send: Callable) -> None:
    body = json.dumps(
        {
            "error": {
                "code": "request_body_too_large",
                "message": "Corpo da requisição excede o limite permitido.",
                "details": None,
            }
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
