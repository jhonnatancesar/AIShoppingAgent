"""Restart de processo, rollback real e falhas HTTP controladas da 118H."""

import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import redis
from app.ai_provider.contracts import (
    AIMessage,
    AIMessageRole,
    AIProviderError,
    AIRequest,
)
from app.ai_provider.manager import build_admin_dev_ai_provider_manager
from app.core.config import Settings
from app.core.resilience import RetryPolicy
from app.search import firecrawl as firecrawl_module
from app.search.contracts import WebSearchError
from app.search.firecrawl import FirecrawlSearchProvider
from app.search.manager import build_web_search_manager
from app.users.models import UserRole
from argon2 import PasswordHasher
from pydantic import SecretStr

pytestmark = pytest.mark.skipif(
    os.environ.get("AISHOPPING_RUN_118H_RECOVERY") != "1",
    reason="Exige stack DEV e harness de processos 118H",
)
GG = Path(__file__).resolve().parents[1]
QUERY = "Python programming language official documentation"
SYSTEM = "Responda somente com a palavra OK."


def assert_private(output):
    files = [GG / ".secrets" / name for name in (
        "cesar-core-client-dev", "firecrawl_api_key", "gemini_api_key_admin_dev",
        "groq_api_key", "openrouter_api_key",
    )]
    files.append(Path(os.environ["CESAR_CORE_118H_SCRIPT"]).parents[1] / ".secrets/omniroute_api_key")
    for path in files:
        assert path.read_text().strip() not in output, "Segredo detectado; valor omitido"
    assert SYSTEM not in output and QUERY not in output


@pytest.fixture(autouse=True)
def private_records(caplog):
    caplog.set_level("INFO")
    yield
    assert_private(caplog.text + repr([record.__dict__ for record in caplog.records]))


def request():
    return AIRequest(uuid4(), UserRole.DEV, "recovery_validation", (
        AIMessage(AIMessageRole.SYSTEM, SYSTEM),
        AIMessage(AIMessageRole.USER, "Confirme a disponibilidade."),
    ), datetime.now(UTC))


class CoreProcess:
    def __init__(self, directory, *, quota=60, deny_search=False, admin_enabled=False):
        self.directory = directory
        self.quota = quota
        self.deny_search = deny_search
        self.process = None
        self.logs = []
        # ADR 0018 (César Core): quota é Redis compartilhado por namespace,
        # não em memória por processo -- sem um namespace único por caso,
        # o contador vaza entre casos de teste da mesma sessão (mesmo
        # padrão de isolamento que os contracts do próprio Core já usam).
        self.quota_namespace = f"cesar-core:test:118h:{uuid4().hex}"
        self.quota_redis_url = "redis://127.0.0.1:16379/0"
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.settings = Settings(_env_file=None, cesar_core_base_url=self.url)
        self.admin_enabled = admin_enabled
        if admin_enabled:
            # Seção 1 (TASK-118H): Control Plane descartável e exclusivo
            # deste caso -- SQLite, credencial e pepper gerados aqui, nunca
            # reaproveitando o ambiente DEV normal nem a quota persistente
            # real do gg_oferta.
            self.admin_password = secrets.token_urlsafe(18)
            self.admin_database = self.directory / "control-plane.sqlite3"
            self.admin_password_hash_file = self.directory / "admin-password-hash"
            self.admin_password_hash_file.write_text(
                PasswordHasher().hash(self.admin_password), encoding="utf-8"
            )
            self.admin_pepper_file = self.directory / "admin-credential-pepper"
            self.admin_pepper_file.write_text(secrets.token_urlsafe(48), encoding="utf-8")
            self.admin_allowed_origin = self.url

    def start(self):
        assert self.process is None
        self.log = (self.directory / f"core-{len(self.logs)}.log").open("wb")
        self.logs.append(Path(self.log.name))
        args = [sys.executable, os.environ["CESAR_CORE_118H_SCRIPT"], "serve",
                "--gg-repo", str(GG), "--port", str(self.port), "--quota", str(self.quota),
                "--quota-namespace", self.quota_namespace]
        if self.deny_search:
            args.append("--deny-search")
        if self.admin_enabled:
            args += [
                "--admin-database", str(self.admin_database),
                "--admin-password-hash-file", str(self.admin_password_hash_file),
                "--admin-pepper-file", str(self.admin_pepper_file),
                "--admin-allowed-origin", self.admin_allowed_origin,
            ]
        self.process = subprocess.Popen(args, stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 90
        with httpx.Client(timeout=5, trust_env=False) as client:
            while time.monotonic() < deadline and self.process.poll() is None:
                try:
                    if client.get(self.url + "/ready").json().get("status") == "ok":
                        return self.process.pid
                except (httpx.HTTPError, ValueError):
                    pass
                time.sleep(0.2)
        pytest.fail("Core não iniciou; logs não exibidos por segurança")

    def stop(self):
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
            self.log.close()
            self.process = None

    def metrics(self):
        return httpx.get(self.url + "/metrics", trust_env=False).text

    def wire(self):
        return [row for row in self.metrics().splitlines() if row.startswith("cesar_core_118h_wire_total")]

    def configure_quota(self, capability, limit):
        """Seção 2 (TASK-118H): configura a política pela API administrativa
        REAL do Control Plane (login -> CSRF -> PUT applications/{id}) --
        nunca escrever diretamente em quota_policies. Confirma via GET que a
        política efetiva ficou como o esperado antes de devolver."""
        assert self.admin_enabled
        with httpx.Client(base_url=self.url, timeout=10, trust_env=False,
                           headers={"Origin": self.admin_allowed_origin}) as client:
            login = client.post("/admin/api/login", json={"password": self.admin_password})
            login.raise_for_status()
            csrf = login.json()["csrf_token"]
            applications = client.get("/admin/api/applications").json()
            current = next(item for item in applications if item["id"] == "gg_oferta")
            quotas = dict(current["quotas"])
            quotas[capability] = limit
            response = client.put(
                f"/admin/api/applications/{current['id']}",
                json={
                    "display_name": current["display_name"],
                    "state": current["state"],
                    "capabilities": current["capabilities"],
                    "quotas": quotas,
                },
                headers={"X-CSRF-Token": csrf},
            )
            response.raise_for_status()
            confirmed = client.get("/admin/api/applications").json()
            effective = next(item for item in confirmed if item["id"] == "gg_oferta")
        assert effective["quotas"][capability] == limit, (
            "Confirmação via API Admin falhou: política efetiva não reflete a mudança"
        )


@pytest.fixture
def core_factory(tmp_path, caplog):
    caplog.set_level("INFO")
    processes = []

    def factory(**kwargs):
        directory = tmp_path / str(len(processes))
        directory.mkdir()
        core = CoreProcess(directory, **kwargs)
        processes.append(core)
        core.start()
        return core

    yield factory
    output = caplog.text + repr([record.__dict__ for record in caplog.records])
    for core in processes:
        core.stop()
        for log in core.logs:
            output += log.read_text(errors="replace")
            log.unlink()
        if core.admin_enabled:
            # Seção 5 (TASK-118H): não deixar persistida a credencial
            # administrativa de teste nem o SQLite descartável do Control
            # Plane -- limpeza explícita, sem depender só do tmp_path do pytest.
            for path in (core.admin_database, core.admin_password_hash_file, core.admin_pepper_file):
                path.unlink(missing_ok=True)
            for suffix in ("-wal", "-shm"):
                Path(str(core.admin_database) + suffix).unlink(missing_ok=True)
    assert_private(output)


def managers(core, **changes):
    settings = core.settings.model_copy(update=changes)
    firecrawl = FirecrawlSearchProvider(settings.firecrawl_api_key,
        retry_policy=RetryPolicy(max_attempts=1))
    return build_admin_dev_ai_provider_manager(settings), build_web_search_manager(settings, firecrawl)


def test_real_core_process_restart_without_restarting_gg(core_factory, monkeypatch):
    core = core_factory()
    ai, search = managers(core)
    initial = request()
    assert asyncio.run(ai.generate(initial)).content.strip()
    assert asyncio.run(search.search(QUERY, limit=3)).provider == "cesar_core"
    first_pid = core.process.pid
    core.stop()
    with pytest.raises(AIProviderError) as failure:
        asyncio.run(ai.generate(request()))
    assert failure.value.code == "cesar_core_connection_unavailable"
    fallback = asyncio.run(search.search(QUERY, limit=3))
    assert fallback.provider == "firecrawl" and fallback.fallback_reason
    assert core.start() != first_pid
    # Os mesmos objetos, sem reset do circuito nem restart do GG Oferta.
    assert asyncio.run(ai.generate(request())).provider == "cesar_core"
    recovered = asyncio.run(search.search(QUERY, limit=3))
    assert recovered.provider == "cesar_core" and recovered.source == "searxng-search"
    assert recovered.fallback_reason is None and 1 <= len(recovered.results) <= 3
    assert initial.messages[0].content == SYSTEM


def test_real_quota_persists_across_core_restart(core_factory):
    """ADR 0018 (César Core): a POLÍTICA de quota (limite) mora no Control
    Plane (SQLite), configurada aqui pela API administrativa real -- nunca
    escrita direto em quota_policies. O CONSUMO (contador da janela de 60s)
    continua em Redis, compartilhado por namespace e não em memória do
    processo. Reiniciar o Core NÃO recria a janela nem devolve admissões já
    consumidas. Este teste substitui a expectativa antiga (quota em memória
    via env var, resetava no restart); ver
    docs/adr/0018-persistent-quota-recovery.md no repositório do César Core,
    "O teste histórico GG que esperava reset de quota é evidência da
    limitação antiga e não vale mais como comportamento esperado"."""
    core = core_factory(admin_enabled=True)
    core.configure_quota("ai", 1)
    ai, _search = managers(core)
    assert asyncio.run(ai.generate(request())).content.strip()
    wire_before = core.wire()
    key = f"{core.quota_namespace}:gg_oferta:ai"
    with redis.Redis.from_url(core.quota_redis_url) as store:
        counter, ttl = store.get(key), store.pttl(key)
    assert counter == b"1" and ttl > 0
    with pytest.raises(AIProviderError) as ai_error:
        asyncio.run(ai.generate(request()))
    assert not ai_error.value.retryable
    assert ai_error.value.code == "cesar_core_request_failed"
    assert core.wire() == wire_before
    assert (
        'cesar_core_http_requests_total{application="gg_oferta",method="POST",'
        'path="/v1/ai/generate",status="429"} 1'
    ) in core.metrics()
    # Restart real do processo -- nunca resetar/apagar o contador Redis aqui
    # para "simular" recuperação: a prova precisa vir de um restart de
    # verdade, com o mesmo namespace isolado deste caso. wire() é por
    # processo (zera no restart, é in-memory do novo processo) -- a base
    # para "zero chamadas upstream" pós-restart precisa ser recapturada
    # aqui, não comparada contra o processo anterior.
    core.stop()
    core.start()
    wire_after_restart = core.wire()
    with redis.Redis.from_url(core.quota_redis_url) as store:
        counter_after_restart, ttl_after_restart = store.get(key), store.pttl(key)
    assert counter_after_restart == b"1" and 0 < ttl_after_restart <= ttl
    with pytest.raises(AIProviderError) as ai_error_after_restart:
        asyncio.run(ai.generate(request()))
    assert not ai_error_after_restart.value.retryable
    assert ai_error_after_restart.value.code == "cesar_core_request_failed"
    assert core.wire() == wire_after_restart


def test_real_invalid_credential_and_capability_have_no_upstream(core_factory, tmp_path):
    core = core_factory(deny_search=True)
    key = tmp_path / "invalid-key"
    key.write_text("synthetic-invalid-118h")
    ai, search = managers(core, cesar_core_api_key_file=key)
    before = core.wire()
    with pytest.raises(AIProviderError) as error:
        asyncio.run(ai.generate(request()))
    assert not error.value.retryable
    with pytest.raises(WebSearchError):
        asyncio.run(search.search(QUERY, limit=3))
    _, allowed_key_search = managers(core)
    with pytest.raises(WebSearchError) as denied:
        asyncio.run(allowed_key_search.search(QUERY, limit=3))
    assert not denied.value.retryable and core.wire() == before
    assert 'status="401"' in core.metrics() and 'status="403"' in core.metrics()


def test_real_configuration_rollback_and_reenable(core_factory):
    core = core_factory()
    ai, search = managers(core)
    assert asyncio.run(ai.generate(request())).provider == "cesar_core"
    assert asyncio.run(search.search(QUERY, limit=3)).provider == "cesar_core"
    # Reload de Settings/factories: equivalente à configuração aplicada no restart
    # do processo consumidor. Não altera .env, banco ou serviços compartilhados.
    legacy = Settings(_env_file=None, cesar_core_ai_enabled=False,
        cesar_core_search_enabled=False,
        gemini_api_key_admin_dev_file=GG / ".secrets/gemini_api_key_admin_dev",
        groq_api_key_file=GG / ".secrets/groq_api_key",
        openrouter_api_key_file=GG / ".secrets/openrouter_api_key")
    firecrawl = FirecrawlSearchProvider(legacy.firecrawl_api_key,
        retry_policy=RetryPolicy(max_attempts=1))
    before = core.wire()
    old_ai = asyncio.run(build_admin_dev_ai_provider_manager(legacy).generate(request()))
    old_search = asyncio.run(build_web_search_manager(legacy, firecrawl).search(QUERY, limit=3))
    assert old_ai.provider in {"gemini", "groq", "openrouter"} and old_ai.content.strip()
    assert old_search.provider == "firecrawl" and old_search.fallback_reason is None
    assert old_search.results and core.wire() == before
    ai_again, search_again = managers(core)
    assert asyncio.run(ai_again.generate(request())).provider == "cesar_core"
    assert asyncio.run(search_again.search(QUERY, limit=3)).source == "searxng-search"


@contextmanager
def fault_server(status, *, malformed=False):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            calls.append(self.path)
            body = b"not-json" if malformed else json.dumps({"error": {"code": "controlled_fault"}}).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.mark.parametrize("status,malformed", [(400, False), (200, True)])
def test_controlled_http_rejection_has_no_search_fallback(status, malformed, monkeypatch):
    with fault_server(status, malformed=malformed) as (url, calls):
        monkeypatch.setattr(firecrawl_module, "_SEARCH_ENDPOINT", url + "/v2/search")
        settings = Settings(_env_file=None, cesar_core_base_url=url)
        fallback = FirecrawlSearchProvider(SecretStr("synthetic-118h-key"))
        manager = build_web_search_manager(settings, fallback)
        with pytest.raises(WebSearchError) as failure:
            asyncio.run(manager.search(QUERY, limit=3))
        assert not failure.value.retryable and calls == ["/v1/search"]


def test_controlled_firecrawl_outage_is_bounded(monkeypatch):
    with fault_server(503) as (url, calls):
        monkeypatch.setattr(firecrawl_module, "_SEARCH_ENDPOINT", url + "/v2/search")
        settings = Settings(_env_file=None, cesar_core_base_url=url)
        firecrawl = FirecrawlSearchProvider(SecretStr("synthetic-118h-key"),
            retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=0.01, max_delay_seconds=0.01))
        manager = build_web_search_manager(settings, firecrawl)
        with pytest.raises(WebSearchError) as failure:
            asyncio.run(manager.search(QUERY, limit=3))
        assert failure.value.code == "firecrawl_search_failed"
        assert calls == ["/v1/search", "/v2/search", "/v2/search", "/v2/search"]
