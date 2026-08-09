# Limites e resiliência

A TASK-049 protege as fronteiras já existentes da V1 sem adicionar fila,
cache ou coordenação distribuída.

## Entrada HTTP e Telegram

- corpos HTTP acima de 64 KiB são rejeitados com `413` antes do parsing;
- cada `update_id` autenticado gera no máximo um recibo append-only;
- recibo `accepted` e efeitos de domínio pertencem à mesma transação;
- `rate_limited` e `discarded` são recibos terminais e retornam `204`;
- cada usuário aceita no máximo 20 updates por minuto; replay não consome cota
  nem repete aviso, IA ou efeito de domínio;
- não existe estado persistente `processing`: falha inesperada desfaz recibo e
  efeitos, deixando a reentrega novamente elegível.

## Chamadas externas

Toda integração possui timeout explícito. Retry é permitido somente depois de
classificar a operação como segura/idempotente. Leituras GET de Store Providers
podem repetir falhas transitórias com backoff exponencial e jitter. Envio
`sendMessage` é potencialmente não idempotente e nunca recebe retry cego:
timeout depois de envio incerto é ambíguo e exige decisão posterior sem duplicar
a mensagem automaticamente.

`Retry-After` inválido é ignorado; valor válido é limitado pelo teto local. Não
há `sleep` dentro de transação ou enquanto um evento aguarda `next_retry_at`.
A cascata ADMIN/DEV possui no máximo três tentativas globais, incluindo fallback;
USER continua sem fallback.

Circuit breakers vivem somente na memória de cada processo e têm chaves
independentes para Telegram Bot API, provider/modelo de IA e Store Provider.
Timeout, transporte, 408, 429 transitório e 5xx contam; validação, domínio e
falhas permanentes como 400/401/403 não contam. Half-open admite uma única
sonda por circuito/processo. A ausência de coordenação entre réplicas é uma
limitação deliberada da V1.

## Eventos e worker

`event_consumption_attempts` permanece append-only. Uma falha registra
`next_retry_at`; o claim ignora o evento até o horário, sem esperar. Erro
permanente ou limite esgotado acrescenta `dead_lettered`. `succeeded`, `skipped`
e `dead_lettered` são terminais por consumidor, protegidos por índice único
parcial no PostgreSQL.

O worker faz rollback em falha inesperada, dorme somente depois de encerrar a
transação e usa backoff exponencial limitado antes do próximo lote. Métricas de
resiliência usam somente `component` e `event` em allowlists pequenas. As
regras Prometheus detectam dead letters e circuitos abertos, mas não enviam
notificações sem Alertmanager.

## Configuração

As opções estão documentadas em `.env.example` e `backend/.env.example`. Os
principais padrões são 64 KiB, 20 updates/minuto, timeout externo de 10 s,
três tentativas seguras, circuito após cinco falhas por 30 s e cinco tentativas
de evento com backoff de 60 a 900 s.
