# Monitor de Eventos

Eventos registram mudanças relevantes, como nova coleta, queda de preço, alteração de disponibilidade e transições de missão. O monitor deverá produzir eventos rastreáveis e permitir notificações desacopladas.

Os nomes, agregados e payloads permitidos na V1 estão definidos em
`docs/EVENT_CATALOG.md` e no contrato executável `app.events`. Persistência
e publicação foram implementadas na TASK-043
(`app.events.service.publish_event`, tabela `events` append-only). Detecção
de fatos além dos alertas de preço (TASK-027), consumo e notificações
permanecem nas tarefas próprias — ver `docs/PRICE_ALERTS.md` e
`docs/tasks/TASK-043.md`.
