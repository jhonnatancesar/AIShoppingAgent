# Alertas de preço

A TASK-027 avalia uma nova observação persistida no contexto de uma missão ativa
e produz candidatos de evento do catálogo da V1. Ela não grava nem publica
eventos e não envia notificações.

## Regras

- `price.decreased.v1` é produzido quando `total_amount` diminui em relação à
  observação anterior disponível e comparável da mesma oferta e moeda.
- `price.target_reached.v1` é produzido quando uma observação disponível fica
  menor ou igual ao total-alvo da missão. Após atingir o alvo, novas observações
  que continuem abaixo dele não repetem o alerta; uma nova passagem de cima para
  baixo pode alertar novamente.
- A primeira observação disponível já dentro do alvo produz o alerta; uma
  observação anterior indisponível não suprime esse fato.
- Missões fora de `active` e ofertas `unavailable` ou `unknown` não alertam.
- Moedas diferentes nunca são comparadas nem convertidas.
- Missões sem preço-alvo ainda podem produzir o evento de queda.

O total usa `Decimal` e corresponde ao item somado ao frete conhecido. Frete nulo
não é tratado como frete grátis; mantém a semântica definida em
`docs/PRICE_ENGINE.md`.

O resultado é um `PriceAlertCandidate` validado contra `app.events`.
Persistência e publicação foram implementadas na TASK-043
(`app.events.service.publish_event`) — quem tiver um `PriceAlertCandidate`
desempacota seus campos (`event_type`, `aggregate_type`, `aggregate_id`,
`payload`) na chamada. A TASK-044 implementou o consumo genérico at-least-once
por consumidor (`docs/EVENT_CONSUMPTION.md`); o consumidor concreto e a
notificação Telegram pertencem à TASK-036.
