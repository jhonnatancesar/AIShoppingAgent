# Motor de Compra

O módulo `app.purchase` inicia o fluxo de compra somente pela recomendação
determinística da TASK-038. Ele é somente leitura: não persiste recomendação,
não cria reserva, não abre checkout e não executa ação financeira.

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

## Fronteiras das próximas tarefas

- TASK-039: comparação completa e ordenada das ofertas.
- TASK-040: confirmação explícita antes de qualquer fluxo assistido de compra.
- TASK-041: trilha persistente de compra.

Nenhuma dessas responsabilidades é antecipada pela TASK-038. IA, Telegram, API
HTTP, eventos e notificações também permanecem fora deste módulo.
