# Logging Estruturado

A aplicação escreve eventos JSON, um por linha, em `stdout`. Esse formato permite leitura local pelo Docker e futura coleta por uma plataforma de observabilidade sem acoplar o código a um fornecedor.

## Campos básicos

Todo evento contém:

- `timestamp`: instante UTC em ISO 8601;
- `level`: nível textual do evento;
- `logger`: origem lógica;
- `message`: código estável do evento.

Contextos adicionais são incluídos como campos no mesmo objeto. Exceções registradas com `logger.exception` acrescentam o campo `exception`.

## Requisições HTTP

Requisições concluídas usam `message` igual a `http_request_completed` e incluem:

- `http_method`;
- `http_path`;
- `http_status_code`;
- `duration_ms`.

Falhas usam `http_request_failed`, preservam método, caminho e duração e incluem a exceção formatada. Query strings, corpos, cabeçalhos, tokens, senhas e credenciais não são registrados.

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

Para acompanhar eventos no ambiente local:

```powershell
docker compose logs --follow api
```

Métricas, tracing, correlação distribuída, retenção e envio para serviços externos não pertencem a esta etapa e serão tratados nas tarefas específicas de observabilidade e operação.
