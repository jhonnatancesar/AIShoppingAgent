import pytest
from scripts.register_telegram_webhook import _require_success


def test_webhook_registration_accepts_explicit_telegram_success() -> None:
    response = {"ok": True, "result": True}

    assert _require_success("setWebhook", response) is response


def test_webhook_registration_exits_nonzero_on_bot_api_rejection() -> None:
    with pytest.raises(SystemExit, match="error_code=400"):
        _require_success(
            "setWebhook",
            {
                "ok": False,
                "error_code": 400,
                "description": "sensitive external diagnostic",
            },
        )


def test_webhook_registration_does_not_echo_external_description() -> None:
    with pytest.raises(SystemExit) as captured:
        _require_success(
            "setWebhook",
            {"ok": False, "error_code": 400, "description": "canary-secret"},
        )

    assert "canary-secret" not in str(captured.value)
