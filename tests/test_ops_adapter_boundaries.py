"""Contratos dos adapters operacionais, sem executar serviços reais."""

import hashlib
import hmac
import json
from unittest.mock import Mock

import pytest
from app import ops_controller as ops


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


@pytest.mark.parametrize(
    "operation,payload,expected",
    [
        ("status", {"task_state": "Running"}, "running"),
        ("status", {"task_state": "Ready"}, "stopped"),
        ("status", {"task_state": "Unavailable"}, "unavailable"),
        ("status", {"task_state": "unexpected"}, "unknown"),
        ("start", {"status": "already_running"}, "running"),
        ("restart", {"triggered": True}, "running"),
        ("start", {"triggered": False}, "unknown"),
    ],
)
def test_native_worker_requests_are_signed_and_report_actual_state(
    monkeypatch, operation, payload, expected
):
    key = b"test-only-signing-key"
    monkeypatch.setattr(ops, "_windows_ops_agent_secret", lambda: key)
    monkeypatch.setattr(ops.time, "time", lambda: 1234567890)
    monkeypatch.setattr(ops.secrets_module, "token_hex", lambda size: "test-nonce")
    request = Mock(return_value=Response(payload))
    monkeypatch.setattr(ops.urllib.request, "urlopen", request)
    result = ops.WindowsOpsAgentAdapter().execute(
        ops.Command(service="collection_worker", operation=operation)
    )
    assert result == {"service": "collection_worker", "status": expected}
    sent = request.call_args.args[0]
    assert sent.full_url.endswith(f"/v1/collection_worker/{operation}")
    assert sent.method == "POST" and sent.data == b""
    expected_signature = hmac.new(
        key, b"1234567890.test-nonce.", hashlib.sha256
    ).hexdigest()
    assert sent.get_header("X-ops-signature") == expected_signature
    assert request.call_args.kwargs["timeout"] == 5


@pytest.mark.parametrize(
    "state,operation,expected,action",
    [
        ("exited", "start", "running", "start"),
        ("running", "start", "healthy", None),
        ("running", "restart", "healthy", "restart?t=10"),
        ("paused", "status", "stopped", None),
        ("unexpected", "status", "unknown", None),
    ],
)
def test_docker_adapter_limits_actions_to_single_identified_service(
    monkeypatch, state, operation, expected, action
):
    calls = []

    def request(sent, **kwargs):
        calls.append(sent)
        if sent.method == "GET":
            return Response(
                [
                    {
                        "Id": "test-container",
                        "State": state,
                        "Status": "Up (healthy)" if state == "running" else "Exited",
                    }
                ]
            )
        return Response({})

    monkeypatch.setattr(ops.urllib.request, "urlopen", request)
    result = ops.DockerOpsAdapter().execute(
        ops.Command(service="telegram_notifier", operation=operation)
    )
    assert result == {"service": "telegram_notifier", "status": expected}
    assert len(calls) == (2 if action else 1)
    assert "com.docker.compose.service" in ops.urllib.parse.unquote(calls[0].full_url)
    if action:
        assert calls[1].full_url.endswith(f"/containers/test-container/{action}")
        assert calls[1].method == "POST"


@pytest.mark.parametrize("payload", [[], [{"Id": "one"}, {"Id": "two"}], {}])
def test_ambiguous_or_absent_container_never_receives_mutation(monkeypatch, payload):
    request = Mock(return_value=Response(payload))
    monkeypatch.setattr(ops.urllib.request, "urlopen", request)
    result = ops.DockerOpsAdapter().execute(
        ops.Command(service="telegram_notifier", operation="restart")
    )
    assert result["status"] == "unavailable"
    request.assert_called_once()
