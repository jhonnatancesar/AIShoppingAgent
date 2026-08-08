# RFC-005 — Compra

O fluxo é dividido em responsabilidades independentes:

- TASK-038: recomendação determinística e somente leitura por missão ativa,
  concluída em `app.purchase` (`DEC-026`);
- TASK-039: comparação completa e ordenada de ofertas, concluída em
  `app.purchase` com a mesma elegibilidade e ordenação da TASK-038 (`DEC-027`);
- TASK-040: confirmação explícita e temporária, concluída em `app.purchase`,
  vinculada ao proprietário e à observação exata, com TTL e revalidação
  integral (`DEC-028`);
- TASK-041: trilha persistente da compra.

A recomendação e a comparação não executam ação financeira. Frete desconhecido
não é zero e fica sem total, moedas diferentes não são comparadas e vendedor é
evidência opcional. A posição 1 da comparação é sempre a recomendação para os
mesmos dados.

A confirmação não persiste autorização nem executa compra. Somente `confirm` e
`cancel` são decisões válidas; expiração ou mudança da evidência retorna
`stale`. A persistência do fluxo permanece na TASK-041.
