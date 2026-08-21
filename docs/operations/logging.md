# Logging Estruturado

A aplicação escreve eventos JSON, um por linha, em `stdout`. Esse formato permite leitura local pelo Docker e futura coleta por uma plataforma de observabilidade sem acoplar o código a um fornecedor.

## Campos básicos

Todo evento contém:

- `timestamp`: instante UTC em ISO 8601;
- `level`: nível textual do evento;
- `logger`: origem lógica;
- `message`: código estável do evento.
- `service` e `environment`;
- `request_id`, `trace_id` e `span_id` quando houver contexto ativo.

Contextos adicionais são incluídos como campos no mesmo objeto. Exceções
acrescentam somente `exception_type`, com a classe segura. Mensagem bruta e
traceback não são emitidos, inclusive em produção.

## Requisições HTTP

Requisições concluídas usam `message` igual a `http_request_completed` e incluem:

- `http_method`;
- `http_route` normalizada;
- `http_status_code`;
- `duration_ms`.

Falhas usam `http_request_failed` e preservam apenas método, rota e duração,
sem mensagem de exceção potencialmente sensível. O access log bruto do
Uvicorn fica desativado. Query strings, corpos, cabeçalhos, tokens, senhas e
credenciais não são registrados.

`X-Request-ID` externo só é aceito dentro do limite de tamanho e como UUID
válido; os demais valores são substituídos. Esse identificador serve apenas
para correlação observacional e nunca para identidade, autenticação,
autorização, idempotência ou chave de domínio.

## Tentativas de IA

Cada chamada de provider usa `message` igual a `ai_provider_attempt` e inclui:

- `ai_request_id`, `ai_profile` e `ai_purpose` para correlação segura;
- `ai_provider` e `ai_model` efetivamente tentados;
- `ai_outcome` e `ai_fallback`;
- `ai_quota_reset_at`, em UTC, somente quando informado pelo provedor.

Prompts, respostas, erros brutos, tokens e credenciais nunca são incluídos. Um
reset ausente permanece `null`; a aplicação não estima nem inventa esse prazo.

## Configuração

`AISHOPPING_LOG_LEVEL` aceita `DEBUG`, `INFO`, `WARNING`, `ERROR` ou `CRITICAL` e usa `INFO` por padrão. O mesmo formato é aplicado aos loggers da aplicação e do Uvicorn.

## Notificações Telegram

O worker emite `telegram_notification_batch` para lotes com eventos e inclui
somente contagens de reivindicados, sucessos, falhas e descartados por
preferência (`notification_skipped`); lotes vazios ficam em
`DEBUG`. Falhas conhecidas emitem `telegram_notification_failed` com `event_id`
e código sanitizado. Chat, usuário, texto, payload, resposta bruta e token não
são registrados.

Para acompanhar eventos no ambiente local:

```powershell
docker compose logs --follow api
docker compose logs --follow telegram_notifier
```

Campos cujo nome indique Telegram/user ID, nome, username, e-mail, URL, texto
livre, query, payload, token, senha/hash ou credencial são removidos pelo
formatador. Os containers usam `json-file` com `max-size=10m` e `max-file=5`.
Esse limite é operacional, não política jurídica definitiva. Métricas e tracing
estão documentados em `docs/operations/observability.md`; o inventário completo está em
`docs/architecture/privacy.md`.
