# ADR-007 — Separar confirmação imutável de sua trilha append-only

Status: aceito.

## Contexto

A TASK-040 criou o contrato temporário de confirmação, mas uma FK real para a
trilha exige uma entidade persistente. Um campo mutável de status duplicaria o
estado terminal e dificultaria auditoria, idempotência e recuperação após
reinício.

## Decisão

Persistir a solicitação original em `purchase_confirmations`, imutável e sem
status, e registrar sua criação/resolução em `purchase_trail_entries`, também
imutável. A criação insere confirmação + `requested` na mesma transação. Cada
confirmação admite no máximo um terminal, garantido por índice único parcial.

O serviço usa SAVEPOINT para tratar somente a corrida desse índice: mesma
decisão retorna o vencedor; decisão divergente gera conflito. A observação
original permanece como proveniência, enquanto a confirmação compara os dados
materiais correntes. Novo UUID com conteúdo equivalente continua válido.

## Consequências

- reinícios não perdem confirmações pendentes;
- a resolução é recuperável, idempotente e auditável sem status mutável;
- `confirmed` significa somente consentimento, sem compra ou ação financeira;
- FKs `RESTRICT` e triggers impedem remover ou reescrever a evidência;
- consultas de estado precisam derivar o terminal da trilha.
