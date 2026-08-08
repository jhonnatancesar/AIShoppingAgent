# Monitor de Eventos

Eventos registram mudanças relevantes, como nova coleta, queda de preço, alteração de disponibilidade e transições de missão. O monitor produz fatos rastreáveis e oferece uma fronteira desacoplada para consumidores futuros.

Os nomes, agregados e payloads permitidos na V1 estão definidos em
`docs/EVENT_CATALOG.md` e no contrato executável `app.events`. Persistência
e publicação foram implementadas na TASK-043
(`app.events.service.publish_event`, tabela `events` append-only). A TASK-044
implementou a reivindicação concorrente e o histórico append-only de tentativas
por consumidor; o contrato at-least-once e seus limites estão em
`docs/EVENT_CONSUMPTION.md`. Detecção de fatos além dos alertas de preço
(TASK-027) permanecem nas tarefas próprias. A TASK-036 adicionou o consumidor
concreto dos dois alertas de preço e sua entrega Telegram; ver
`docs/PRICE_ALERTS.md`, `docs/EVENT_CONSUMPTION.md` e as TASKs 043–044.
