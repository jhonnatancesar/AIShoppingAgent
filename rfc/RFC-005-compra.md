# RFC-005 — Compra

O fluxo é dividido em responsabilidades independentes:

- TASK-038: recomendação determinística e somente leitura por missão ativa,
  concluída em `app.purchase` (`DEC-026`);
- TASK-039: comparação completa e ordenada de ofertas;
- TASK-040: confirmação explícita antes de compra assistida;
- TASK-041: trilha persistente da compra.

A recomendação não executa ação financeira. Frete desconhecido não é zero,
moedas diferentes não são comparadas e vendedor é evidência opcional.
