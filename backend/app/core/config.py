"""Configuração tipada da aplicação baseada em variáveis de ambiente."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.urls import normalize_loopback_http_endpoint

BACKEND_DIRECTORY = Path(__file__).resolve().parents[2]

_SECRET_FILE_FIELDS = {
    "database_password": "database_password_file",
    "gemini_api_key_user": "gemini_api_key_user_file",
    "gemini_api_key_admin_dev": "gemini_api_key_admin_dev_file",
    "groq_api_key": "groq_api_key_file",
    "openrouter_api_key": "openrouter_api_key_file",
    "firecrawl_api_key": "firecrawl_api_key_file",
    "telegram_bot_token": "telegram_bot_token_file",
    "ops_controller_secret": "ops_controller_secret_file",
    "telegram_webhook_secret": "telegram_webhook_secret_file",
}
_MAX_SECRET_FILE_BYTES = 16 * 1024


class Settings(BaseSettings):
    """Valores de configuração carregados do ambiente local ou do processo."""

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIRECTORY / ".env",
        env_file_encoding="utf-8",
        env_prefix="AISHOPPING_",
        extra="ignore",
    )

    app_name: str = "AIShoppingAgent"
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_host: str = Field(default="localhost", min_length=1)
    database_port: int = Field(default=5432, ge=1, le=65535)
    database_name: str = Field(default="aishoppingagent", min_length=1)
    database_user: str = Field(default="aishoppingagent", min_length=1)
    database_password: SecretStr | None = None
    database_password_file: Path | None = None
    gemini_api_key_user: SecretStr | None = None
    gemini_api_key_user_file: Path | None = None
    gemini_api_key_admin_dev: SecretStr | None = None
    gemini_api_key_admin_dev_file: Path | None = None
    gemini_model: str = Field(default="gemini-3.6-flash", min_length=1)
    # TASK-083: modelo dedicado só para requisições que exigem
    # `require_search_grounding=True` -- nunca usado nas chamadas comuns,
    # que continuam com `gemini_model`. Separado porque grounding via
    # Google Search é uma capability específica, não simplesmente "a
    # versão mais nova do Gemini".
    gemini_grounding_model: str = Field(default="gemini-2.5-flash", min_length=1)
    groq_api_key: SecretStr | None = None
    groq_api_key_file: Path | None = None
    groq_model: Literal["openai/gpt-oss-120b"] = "openai/gpt-oss-120b"
    openrouter_api_key: SecretStr | None = None
    openrouter_api_key_file: Path | None = None
    openrouter_free_model: Literal["openrouter/free"] = "openrouter/free"
    firecrawl_api_key: SecretStr | None = None
    firecrawl_api_key_file: Path | None = None
    telegram_bot_token: SecretStr | None = None
    telegram_bot_token_file: Path | None = None
    telegram_webhook_secret: SecretStr | None = None
    telegram_webhook_secret_file: Path | None = None
    ops_controller_url: str | None = None
    ops_controller_secret: SecretStr | None = None
    ops_controller_secret_file: Path | None = None
    telegram_notification_poll_seconds: float = Field(default=5.0, gt=0, le=3600)
    telegram_notification_batch_size: int = Field(default=50, ge=1, le=1000)
    collection_poll_seconds: float = Field(default=15.0, gt=0, le=3600)
    collection_batch_size: int = Field(default=25, ge=1, le=1000)
    collection_schedule_interval_minutes: int = Field(default=60, ge=1, le=10080)
    collection_schedule_stagger_seconds: int = Field(default=300, ge=0, le=3600)
    collection_stale_run_minutes: int = Field(default=10, ge=1, le=1440)
    collection_max_concurrency: int = Field(default=4, ge=1, le=4)
    # TASK-079: airbags de banco só para a conexão assíncrona dedicada do
    # collection_worker (não alteram postgresql.conf nem outros serviços).
    # Não são a correção do autodeadlock -- a correção é a fronteira
    # transacional (fase A/B/C); estes valores só garantem que um bug
    # futuro que reintroduza um await externo dentro de transação seja
    # abortado pelo Postgres em vez de travar o worker para sempre.
    collection_lock_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    collection_statement_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    collection_idle_in_transaction_timeout_seconds: float = Field(
        default=10.0, gt=0, le=60
    )
    # TASK-079: teto de tempo para uma claim inteira (coleta + fase A + IA
    # fora de transação + fase C) -- nenhuma claim trava o worker para
    # sempre, mesmo diante de um bug futuro não coberto pelos timeouts
    # específicos (navegação, HTTP, IA, banco) já existentes.
    collection_claim_deadline_seconds: float = Field(default=300.0, gt=0, le=1800)
    # Extensão da TASK-079: mesmos airbags de banco, agora para a conexão
    # assíncrona dedicada ao webhook Telegram/API -- não alteram
    # postgresql.conf nem outros serviços. A seção crítica deste caminho é
    # pequena (dedupe/resolução de usuário/reserva, ou revalidação/persistência),
    # por isso os limites são mais curtos que os do collection_worker.
    telegram_lock_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    telegram_statement_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    telegram_idle_in_transaction_timeout_seconds: float = Field(
        default=5.0, gt=0, le=30
    )
    # Extensão da TASK-079: teto de tempo para o processamento inteiro de um
    # update do Telegram (fase A + IA fora de transação + fase C + envio da
    # resposta). Calculado a partir do pior caso real, não arbitrário:
    #   Fase A + Fase C (banco, 2 seções curtas):
    #       2 x telegram_statement_timeout_seconds (10s)      = 20s
    #   Fase B (IA, cascata Gemini -> Groq, sem espera entre
    #       tiers -- AdminDevAIProviderManager não faz backoff
    #       entre provedores, só tenta o próximo):
    #       2 x external_http_timeout_seconds (10s)           = 20s
    #   Fase D (envio Telegram, tentativa única via
    #       asyncio.to_thread, sem retry interno):
    #       1 x external_http_timeout_seconds (10s)           = 10s
    #   Núcleo: 20s + 20s + 10s = 50s
    #   Margem operacional (~80%, contenção de pool/scheduling
    #       sob carga): ~40s
    #   Total: 90s
    telegram_message_deadline_seconds: float = Field(default=90.0, gt=0, le=300)
    max_request_body_bytes: int = Field(default=65_536, ge=1024, le=1_048_576)
    telegram_rate_limit_per_minute: int = Field(default=20, ge=1, le=1000)
    external_http_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    # TASK-075 (correção): timeout de navegação do Playwright (`page.goto`),
    # desacoplado de `external_http_timeout_seconds` -- carregar uma página
    # completa via Chromium (headed, para lojas que exigem, ex.: Pichau/
    # Terabyte) é uma operação estruturalmente diferente e mais lenta que
    # uma chamada de API externa; reaproveitar o mesmo timeout curto causava
    # falha real (ProviderNavigationError) em lojas cuja página demora mais
    # que 10s para atingir domcontentloaded.
    browser_navigation_timeout_seconds: float = Field(default=45.0, gt=0, le=120)
    # TASK-105: nome genérico -- o mesmo Edge/CDP supervisionado passou a
    # ser reaproveitado pela Terabyte (transporte primário/único, DEC-070)
    # além da Magalu (transporte primário/único) e do Mercado Livre
    # (fallback de último recurso). `AISHOPPING_MAGALU_CDP_URL` (nome
    # histórico) continua aceito por compatibilidade; `edge_cdp_url` puro
    # também funciona para construção direta (testes/scripts).
    edge_cdp_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "edge_cdp_url", "AISHOPPING_EDGE_CDP_URL", "AISHOPPING_MAGALU_CDP_URL"
        ),
    )
    magalu_edge_executable: Path | None = None
    magalu_edge_profile_dir: Path | None = None
    magalu_edge_startup_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    magalu_edge_probe_interval_seconds: float = Field(default=1.0, gt=0, le=30)
    magalu_cdp_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    magalu_cdp_navigation_timeout_seconds: float = Field(default=20.0, gt=0, le=60)
    magalu_cdp_document_timeout_seconds: float = Field(default=10.0, gt=0, le=30)
    magalu_cdp_html_timeout_seconds: float = Field(default=3.0, gt=0, le=15)
    safe_retry_max_attempts: int = Field(default=3, ge=1, le=5)
    retry_base_delay_seconds: float = Field(default=0.25, gt=0, le=10)
    retry_max_delay_seconds: float = Field(default=5.0, gt=0, le=60)
    retry_after_cap_seconds: float = Field(default=30.0, gt=0, le=300)
    circuit_failure_threshold: int = Field(default=5, ge=1, le=20)
    circuit_open_seconds: float = Field(default=30.0, gt=0, le=300)
    availability_fallback_max_candidates: int = Field(default=3, ge=0, le=10)
    event_consumer_max_attempts: int = Field(default=5, ge=1, le=20)
    event_retry_base_seconds: float = Field(default=60.0, gt=0, le=3600)
    event_retry_cap_seconds: float = Field(default=900.0, gt=0, le=86400)
    worker_failure_backoff_seconds: float = Field(default=5.0, gt=0, le=300)
    observability_enabled: bool = False
    otel_exporter_otlp_traces_endpoint: str = Field(
        default="http://localhost:4318/v1/traces", min_length=1
    )
    trace_sample_ratio: float = Field(default=1.0, ge=0, le=1)
    readiness_timeout_seconds: float = Field(default=1.0, gt=0, le=10)
    worker_metrics_port: int = Field(default=9464, ge=1, le=65535)
    auth_public_base_url: str = Field(default="http://localhost:8000", min_length=1)
    # TASK-091 (item 1 da V1.2): diretório do build estático da SPA
    # (`frontend/dist`, gerado por `npm run build`). `None` (padrão) resolve
    # para o `frontend/dist` do próprio checkout, relativo a este arquivo --
    # funciona em dev local sem configuração. Se o diretório não existir no
    # startup, a rota de fallback da SPA responde 404 em vez de derrubar a
    # aplicação (API/Telegram continuam funcionando mesmo sem build do
    # frontend). Empacotamento Docker do build ainda não implementado --
    # ver docs/tasks/TASK-091.md.
    spa_dist_dir: Path | None = None
    # TASK-107: defaults de cota por usuário (USER). Override por usuário
    # fica em `User.max_*_override` (NULL = usa este default) -- sem plano/
    # tier novo agora (`DEC-073`/`DEC-094`).
    default_max_active_missions: int = Field(default=5, ge=1, le=1000)
    default_max_store_slots: int = Field(default=18, ge=1, le=10000)
    default_max_daily_searches: int = Field(default=30, ge=1, le=100000)
    quota_warning_threshold: float = Field(default=0.8, gt=0, le=1.0)

    @field_validator("edge_cdp_url")
    @classmethod
    def validate_edge_cdp_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = normalize_loopback_http_endpoint(value)
        if normalized is None:
            raise ValueError("edge_cdp_url must be loopback HTTP with explicit port")
        return normalized

    @model_validator(mode="after")
    def resolve_secret_files(self) -> Settings:
        """Resolve uma única fonte por segredo e proíbe ENV direto em produção."""
        if self.retry_max_delay_seconds < self.retry_base_delay_seconds:
            raise ValueError("retry max delay must not be smaller than base delay")
        if self.event_retry_cap_seconds < self.event_retry_base_seconds:
            raise ValueError(
                "event retry cap must not be smaller than event retry base"
            )
        for secret_field, file_field in _SECRET_FILE_FIELDS.items():
            direct_value = getattr(self, secret_field)
            file_path = getattr(self, file_field)

            if direct_value is not None and not direct_value.get_secret_value().strip():
                raise ValueError(f"{secret_field} must not be empty")
            if direct_value is not None and file_path is not None:
                raise ValueError(
                    f"{secret_field} and {file_field} cannot be configured together"
                )
            if self.environment == "production" and direct_value is not None:
                raise ValueError(
                    f"{secret_field} must be provided through {file_field} in production"
                )
            if file_path is not None:
                setattr(
                    self,
                    secret_field,
                    SecretStr(_read_secret_file(file_path, file_field)),
                )
        return self


def _read_secret_file(path: Path, field_name: str) -> str:
    """Lê um secret pequeno sem normalizar espaços que façam parte do valor."""
    try:
        if not path.is_file():
            raise ValueError(f"{field_name} must reference a regular file")
        if path.stat().st_size > _MAX_SECRET_FILE_BYTES:
            raise ValueError(f"{field_name} exceeds the maximum allowed size")
        value = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"{field_name} could not be read") from error

    value = value.removesuffix("\n").removesuffix("\r")
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ValueError(f"{field_name} must contain exactly one line")
    return value


@lru_cache
def get_settings() -> Settings:
    """Retorna uma instância reutilizável das configurações validadas."""

    return Settings()
