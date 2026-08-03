# Monitor de Eventos

Eventos registram mudanças relevantes, como nova coleta, queda de preço, alteração de disponibilidade e transições de missão. O monitor deverá produzir eventos rastreáveis e permitir notificações desacopladas.

Os nomes, agregados e payloads permitidos na V1 estão definidos em
`docs/EVENT_CATALOG.md` e no contrato executável `app.events`. Detecção,
persistência, publicação, consumo e notificações permanecem nas tarefas próprias.
