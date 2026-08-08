# Motor de Compra

O módulo `app.purchase` inicia o fluxo de compra pela recomendação determinística
da TASK-038, pela comparação ordenada da TASK-039 e pela confirmação temporária
da TASK-040 e pela persistência append-only da TASK-041. Ele não cria reserva,
não abre checkout e não executa ação financeira.

## Fluxo de recomendação

`recommend_for_mission(session, mission_id)` exige uma missão `active` e usa
exclusivamente observações pertencentes a coletas `succeeded` da própria missão
e a fontes presentes em `mission_sources`. A observação mais recente de cada
oferta representa seu estado corrente.

Uma oferta só é elegível quando:

- a observação corrente está `available`;
- sua moeda é exatamente `MissionCriteria.target_currency`;
- `shipping_amount` é conhecido.

Frete nulo significa desconhecido, nunca zero ou grátis. O serviço não converte,
compara nem soma moedas diferentes. Missão sem moeda, histórico ausente ou falta
de qualquer candidata elegível produz `insufficient_data` com uma razão estável.

Entre as elegíveis, vence o menor `total_amount`. Empates usam a observação mais
recente e depois o UUID da oferta. O resultado inclui, sem ordenar como uma
comparação, as evidências encontradas: produto, loja, vendedor opcional, URL,
preço, frete, total, moeda, disponibilidade, fulfillment e horários. O resumo
histórico identifica a observação anterior comparável e o menor total comparável,
além das contagens usadas.

## Fluxo de comparação

`compare_offers_for_mission(session, mission_id)` reutiliza integralmente o
recorte, a elegibilidade, as exclusões e as evidências da recomendação. A única
ordenação compartilhada (`rank_eligible_evidence`) garante que a posição 1 da
comparação seja sempre a oferta recomendada pela TASK-038 para os mesmos dados.

Somente ofertas elegíveis recebem posições consecutivas `1..N`: menor custo
total, observação mais recente e UUID. As inelegíveis ficam depois, sem posição,
e são estabilizadas por loja, produto e UUID — nunca por preço. Frete
desconhecido preserva o preço do produto em `amount`, mas expõe
`total_amount=None` e a exclusão `shipping_unknown`; ele jamais é apresentado
como custo total. Se nenhuma oferta for elegível, o resultado é
`insufficient_data` sem ranking.

## Fluxo de confirmação

`request_purchase_confirmation` aceita qualquer oferta elegível da comparação,
mas somente para o proprietário da missão. A solicitação imutável registra a
missão, o proprietário, a oferta e o `price_observation_id` original, junto ao
snapshot sanitizado de produto, loja, vendedor opcional, URL, preço, frete,
total, moeda, disponibilidade e horário. `requested_at` e `expires_at` usam UTC
e um TTL fixo de 15 minutos. A solicitação e a entrada `requested` são gravadas
na mesma transação.

`resolve_purchase_confirmation` aceita apenas `confirm` ou `cancel`. Para
`confirm`, proprietário e terminal existente são verificados primeiro; no
limite `now >= expires_at`, o resultado é `stale/expired` sem recalcular a
oferta. Dentro do TTL, o serviço exige que oferta, elegibilidade,
disponibilidade, moeda, preço, frete, total e demais campos materiais continuem
equivalentes. Uma observação mais nova com os mesmos dados continua válida; o
UUID original permanece como proveniência. Divergência material produz
`stale/evidence_changed`. `cancel` independe do TTL e da oferta corrente.

`purchase_confirmations` mantém a solicitação imutável, sem status.
`purchase_trail_entries` mantém no máximo uma `requested` e uma resolução
terminal append-only. Repetições equivalentes são idempotentes; decisão
conflitante falha. A corrida terminal usa SAVEPOINT e deixa o índice único
parcial do PostgreSQL como autoridade final. Triggers bloqueiam `UPDATE` e
`DELETE`, e todas as FKs históricas usam `RESTRICT`.

Não há compra, reserva, checkout, evento ou auditoria. IA, Telegram, API HTTP e
notificações também permanecem fora deste módulo.
