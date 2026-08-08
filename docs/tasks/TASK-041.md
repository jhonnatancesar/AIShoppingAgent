# TASK-041 — Criar trilha de confirmação de compra

Status: Concluída

## Objetivo

Persistir a solicitação imutável de confirmação criada pela TASK-040 e sua trilha
append-only de resolução, sem executar compra, reserva, checkout ou qualquer ação
financeira.

## Escopo aprovado

### `purchase_confirmations`

- solicitação imutável identificada pelo mesmo UUID exposto pela TASK-040;
- FKs `RESTRICT` para missão, proprietário, oferta, observação original, produto,
  loja e vendedor opcional;
- snapshot sanitizado da evidência apresentada, URL, posição, disponibilidade,
  fulfillment, preço, frete, total e moeda;
- `requested_at`, `expires_at` com TTL fixo de 15 minutos e `recorded_at`
  definido pelo PostgreSQL;
- nenhuma coluna mutável de status;
- `UPDATE` e `DELETE` bloqueados por trigger.

### `purchase_trail_entries`

- histórico append-only com FKs `RESTRICT` reais para confirmação, missão,
  proprietário, oferta e observação original;
- identidade relacional composta deve coincidir com a confirmação;
- tipos `requested`, `confirmed`, `cancelled` e `stale`;
- no máximo uma entrada `requested` e no máximo uma entrada terminal por
  confirmação, garantidas por índices únicos parciais;
- `UPDATE` e `DELETE` bloqueados por trigger;
- `recorded_at` definido pelo PostgreSQL.

A criação pública insere a confirmação e sua entrada `requested` na mesma
transação. Não existe caminho público para criar uma confirmação sem a entrada
inicial. A atomicidade do serviço garante a existência de exatamente uma
`requested`; o banco garante declarativamente que não haja duplicação.

## Matriz de resolução

- `requested`: `decision = NULL`, `stale_reason = NULL`, `resolved_at = NULL`;
- `confirmed`: `decision = confirm`, sem stale reason, `resolved_at` obrigatório;
- `cancelled`: `decision = cancel`, sem stale reason, `resolved_at` obrigatório;
- `stale`: `decision = confirm`, reason `expired` ou `evidence_changed`,
  `resolved_at` obrigatório.

Para `confirm`, a ordem obrigatória é:

1. validar proprietário e eventual terminal existente;
2. se `now >= expires_at`, registrar `stale/confirm/expired`, sem recalcular a
   oferta;
3. dentro do TTL, recalcular a comparação;
4. evidência materialmente equivalente gera `confirmed`; evidência alterada
   gera `stale/confirm/evidence_changed`.

Para `cancel`, se ainda não houver terminal, registrar `cancelled/cancel` sem
consultar TTL ou evidência. Uma solicitação expirada ainda pode ser cancelada.
Sem tentativa, uma solicitação expirada permanece somente como `requested`.

## Evidência original e evidência corrente

`purchase_confirmations.price_observation_id` preserva para sempre a observação
original apresentada ao usuário. Uma observação corrente com UUID diferente não
invalida a confirmação quando os dados materiais permanecem equivalentes.

A revalidação considera missão, oferta, elegibilidade, disponibilidade, moeda,
preço, frete, total, produto, loja, vendedor, URL e fulfillment. Identificador e
horário de uma nova observação, posição no ranking e histórico não são mudanças
materiais por si sós. A observação original nunca é substituída.

## Idempotência e concorrência

- repetição da mesma decisão retorna o terminal existente;
- decisão terminal conflitante gera erro de domínio;
- a autoridade final contra dois terminais é o índice único parcial do
  PostgreSQL;
- a inserção terminal usa SAVEPOINT (`Session.begin_nested()`);
- somente a violação identificada do índice terminal é tratada: o serviço relê
  o vencedor e retorna a entrada se a decisão for equivalente ou gera conflito
  se for divergente;
- qualquer outro `IntegrityError` continua propagando como falha interna.

## Consultas e recuperação

- recuperar uma confirmação por UUID e proprietário após reinício;
- consultar a trilha de uma missão em ordem determinística;
- derivar resolução apenas da trilha, sem duplicar estado mutável;
- `confirmed` significa apenas consentimento registrado.

## Fora do escopo

- compra, reserva, carrinho, checkout, pagamento ou ação financeira;
- Telegram, API, IA, eventos, `audit_entries` ou scheduler de expiração;
- publicação de eventos ou status mutável na confirmação.

## Validação obrigatória

- migration `20260808_0007`;
- upgrade, downgrade e novo upgrade em PostgreSQL 18 real;
- FKs `RESTRICT`, constraints, índices únicos parciais e triggers reais;
- atomicidade da criação, confirmação, cancelamento inclusive após expiração,
  expiração no limite, evidência alterada e nova observação idêntica;
- repetição idempotente, conflito terminal e concorrência PostgreSQL real;
- recuperação após reinício e consulta da trilha;
- suíte completa, lint e verificação de uma única head Alembic.

## Entrega

Implementada pela revisão `20260808_0007`. O PostgreSQL 18 real aprovou o ciclo
`upgrade → downgrade → upgrade`, metadata sem diff, constraints, FKs `RESTRICT`,
triggers de imutabilidade, atomicidade, recuperação após reinício, expiração,
cancelamento expirado, evidência alterada, observação nova equivalente,
idempotência e corridas terminais idêntica e conflitante. A suíte completa
aprovou 461 testes com cobertura acima de 90%.

Nenhuma compra ou ação financeira foi adicionada.

Próxima tarefa executável: TASK-045.
