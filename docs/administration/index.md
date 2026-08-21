# Administração

Procedimentos para administrar o AIShoppingAgent diretamente, sem depender
do bot — usados quando algo precisa ser inspecionado ou corrigido fora do
fluxo normal do Telegram.

- [Usuários](users.md) — listar, localizar, promover papel, ativar/desativar.
- [Missões](missions.md) — listar, localizar, inspecionar histórico e
  agendamento, correção manual avançada de estado.
- [PostgreSQL](../database/postgresql.md) — como acessar o banco (pré-requisito
  para as duas páginas acima).

Para operar containers, ver logs e reiniciar serviços, veja
[Operações → Containers](../operations/containers.md). Para o runbook
completo do dia a dia (rotina, rollback, diagnóstico), veja o
[Runbook de operação](../operations/runbook.md).

## Ofertas e coletas

Consultas úteis sobre o que já foi coletado, sem alterar dados. Veja
[PostgreSQL](../database/postgresql.md) para o procedimento de acesso.

Ofertas de uma missão (via lojas selecionadas e produtos observados):

```sql
SELECT DISTINCT o.id, p.display_name, p.name, s.code AS loja, o.url
FROM offers o
JOIN products p ON p.id = o.product_id
JOIN stores s ON s.id = o.store_id
JOIN mission_sources ms ON ms.store_id = o.store_id
WHERE ms.mission_id = '<UUID_DA_MISSAO>';
```

Histórico de preço de uma oferta específica:

```sql
SELECT amount, currency, shipping_amount, total_amount, created_at
FROM price_observations
WHERE offer_id = '<UUID_DA_OFERTA>'
ORDER BY created_at DESC;
```

Preço mais recente conhecido de uma oferta:

```sql
SELECT amount, currency, created_at
FROM price_observations
WHERE offer_id = '<UUID_DA_OFERTA>'
ORDER BY created_at DESC
LIMIT 1;
```

Ofertas por loja (`stores.code`: `pichau`, `terabyte`, `amazon`, `kabum`):

```sql
SELECT o.id, p.display_name, o.url, o.created_at
FROM offers o
JOIN stores s ON s.id = o.store_id
WHERE s.code = '<CODIGO_DA_LOJA>'
ORDER BY o.created_at DESC
LIMIT 20;
```

Execuções de coleta mais recentes, com erro:

```sql
SELECT id, mission_id, store_id, status, started_at, finished_at
FROM collection_runs
WHERE status = 'failed'
ORDER BY started_at DESC
LIMIT 20;
```

Últimas execuções de coleta (qualquer status):

```sql
SELECT cr.id, s.code AS loja, cr.status, cr.started_at, cr.finished_at
FROM collection_runs cr
JOIN stores s ON s.id = cr.store_id
ORDER BY cr.started_at DESC
LIMIT 20;
```
