# Motor de Compra

O módulo `app.purchase` inicia o fluxo de compra pela recomendação determinística
da TASK-038, pela comparação ordenada da TASK-039 e pela confirmação temporária
da TASK-040. Ele não persiste esses resultados, não cria reserva, não abre
checkout e não executa ação financeira.

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
missão, o proprietário, a oferta e o `price_observation_id` exato, junto ao
snapshot exibível de produto, loja, vendedor opcional, URL, preço, frete, total,
moeda, disponibilidade e horário. `requested_at` e `expires_at` usam UTC e um
TTL fixo de 15 minutos.

`resolve_purchase_confirmation` aceita apenas `confirm` ou `cancel`. Uma decisão
depois da expiração retorna `stale`. Antes de confirmar, o serviço recalcula a
comparação e exige a mesma oferta, a mesma observação e os mesmos valores de
disponibilidade, moeda, preço, frete e total. Nova observação — mesmo repetindo
os valores — ou qualquer divergência torna a solicitação `stale` e exige outra.
Cancelamento válido retorna `cancelled`; somente a revalidação integral produz
`confirmed`.

Todos os resultados permanecem em memória. Não há compra, reserva, checkout,
evento, auditoria ou proteção durável contra replay.

## Fronteiras das próximas tarefas

- TASK-041: trilha persistente de compra.

Essa persistência não é antecipada pelas TASKs 038 a 040. IA, Telegram, API
HTTP, eventos e notificações também permanecem fora deste módulo.
