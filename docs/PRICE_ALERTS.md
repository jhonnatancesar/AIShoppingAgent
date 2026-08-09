# Alertas de preço

A TASK-027 avalia uma nova observação persistida no contexto de uma missão ativa
e produz candidatos de evento do catálogo da V1. Ela não grava nem publica
eventos e não envia notificações.

## Regras

Na V1 (`DEC-045`), alertas monitoram o preço do produto (`amount`). Frete é
dado adicional quando conhecido e não participa do critério de alerta —
custo final com frete continua exclusivo de `app.purchase`
(recomendação/comparação, TASK-038/039), que não muda.

- `price.decreased.v1` é produzido quando `amount` diminui em relação à
  observação anterior disponível e comparável da mesma oferta e moeda.
- `price.target_reached.v1` é produzido quando uma observação disponível fica
  menor ou igual ao `target_amount` da missão (preço-alvo do produto, sem
  exigir frete). Após atingir o alvo, novas observações que continuem abaixo
  dele não repetem o alerta; uma nova passagem de cima para baixo pode
  alertar novamente.
- A primeira observação disponível já dentro do alvo produz o alerta; uma
  observação anterior indisponível não suprime esse fato.
- Missões fora de `active` e ofertas `unavailable` ou `unknown` não alertam.
- Moedas diferentes nunca são comparadas nem convertidas.
- Frete desconhecido **não bloqueia** alerta: a comparação usa sempre
  `amount`, nunca `total_amount` — misturar as duas bases geraria alerta
  falso quando o frete muda de conhecido para desconhecido (ou vice-versa)
  entre duas observações. Frete nulo nunca é tratado como zero ou grátis;
  ele simplesmente não participa da decisão de alertar.
- Missões sem preço-alvo ainda podem produzir o evento de queda.

Os campos legados do catálogo (`previous_total`/`current_total`/
`target_total`, em `PriceDecreasedPayload`/`PriceTargetReachedPayload`)
carregam o valor de `amount` para os alertas da V1, por compatibilidade de
contrato — não foram renomeados. `total_amount` continua existindo e sendo
persistido normalmente (`docs/PRICE_ENGINE.md`), mas não é a base de
comparação do avaliador.

O resultado é um `PriceAlertCandidate` validado contra `app.events`.
Persistência e publicação foram implementadas na TASK-043
(`app.events.service.publish_event`) — quem tiver um `PriceAlertCandidate`
desempacota seus campos (`event_type`, `aggregate_type`, `aggregate_id`,
`payload`) na chamada. A TASK-044 implementou o consumo genérico at-least-once
por consumidor (`docs/EVENT_CONSUMPTION.md`); o consumidor concreto e a
notificação Telegram foram implementados na TASK-036. A TASK-062 invoca
automaticamente avaliação e publicação após persistir cada lote; quando o
evento durável existe, `telegram_price_alerts_v1` o entrega e registra o
resultado.
