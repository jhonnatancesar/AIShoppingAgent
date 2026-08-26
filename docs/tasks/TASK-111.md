# TASK-111 — Corrigir asserção desatualizada em `test_product_identity.py`

Status: **Concluída (2026-08-26), aguardando commit.** Corrigida como
parte da rodada de correções da TASK-112 fase 3A (o assert desatualizado
apareceu repetidamente na regressão dessa fase). `tests/integration/
test_product_identity.py::test_same_variant_from_all_stores_reuses_one_global_product`
agora espera as 6 lojas reais (`amazon`, `kabum`, `pichau`, `terabyte`,
`magalu`, `mercadolivre`); o restante do teste (dedupe global de Product/
Offer) não precisou de nenhum ajuste -- só a lista esperada estava
desatualizada, confirmado rodando com todas as 6 lojas. Auditoria
confirmou que não há outro assert conhecido do mesmo tipo (comparação
"todas as lojas cadastradas" contra uma lista hardcoded) desatualizado --
`tests/integration/test_schema_integration.py` já tinha as 6 lojas
corretas.

## Objetivo

Corrigir `tests/integration/test_product_identity.py::test_same_variant_from_all_stores_reuses_one_global_product`,
que falha hoje por uma asserção hardcoded desatualizada — não por um bug
real de identidade de produto.

## Contexto / motivação

Achado durante a validação da TASK-108 (retomada da fila justa, 2026-08-24):
rodar a suíte de integração completa (`python scripts/run_integration_tests.py`,
sem filtro de arquivo, só como checagem cruzada) revelou este teste
falhando com:

```
AssertionError: assert {'amazon', 'k...', 'terabyte'} == {'amazon', 'k...', 'terabyte'}
Extra items in the left set:
'magalu'
'mercadolivre'
```

A asserção em `tests/integration/test_product_identity.py` (por volta da
linha 24) espera `{"amazon", "kabum", "pichau", "terabyte"}` como o
conjunto completo de lojas seed — mas as migrations
`20260822_0007_seed_magalu_store.py` e
`20260822_0008_seed_mercadolivre_store.py` (TASK-104A/TASK-104B) já
adicionaram Magalu e Mercado Livre ao seed há várias TASKs. O banco de
integração real tem 6 lojas, não 4; o teste ficou para trás.

Sem relação com fila/throttle de coleta (TASK-108) nem com a migração de
browser (TASK-109) — puramente uma asserção de teste desatualizada,
mesmo padrão do achado que já virou TASK-110 (`docs/architecture/providers.md`,
lista de fontes desatualizada), só que aqui é código de teste, não
documentação.

## Escopo

- Corrigir a asserção para refletir as 6 lojas reais (confirmar a lista
  exata em `backend/app/collection/providers/stores.py` e nas migrations
  de seed);
- conferir se o restante do teste (dedupe de produto/variante global
  entre lojas) precisa de ajuste para acomodar Magalu/Mercado Livre, ou
  se só a lista esperada estava desatualizada;
- rodar via `python scripts/run_integration_tests.py tests/integration/test_product_identity.py`
  para confirmar.

## Fora de escopo

Qualquer mudança de código de produção, comportamento de identidade de
produto ou schema — esta TASK é só a correção do teste.
