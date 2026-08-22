# TASK-100 — Área USER: listagem geral de ofertas relevantes

Status: **Implementada no DEV, aguardando revisão.**

## Objetivo

Criar `/app/offers` para o USER consultar, em uma única tela, ofertas
relevantes já ligadas às suas missões.

## Regra de acesso

O backend exige `WebSession` e `Permission.MISSION_READ`. Uma Offer só entra
quando existe `MissionOfferRelevance → Mission.user_id` do usuário autenticado
com classificação `MATCH` ou `POSSIBLE_MATCH`. O SQL usa `EXISTS`, portanto a
mesma Offer aparece uma única vez mesmo ligada a várias missões. `NO_MATCH`,
missão alheia e Offer sem missão acessível nunca aparecem.

## Dados e comportamento

- schema Pydantic resumido e explícito, sem evidência bruta ou IDs de coleta;
- última `PriceObservation` por `observed_at DESC, id DESC`;
- imagem, produto, loja, vendedor, nota real, preço/total, condição,
  disponibilidade e atualização quando existentes;
- paginação determinística e filtros por texto, loja, condição e
  disponibilidade;
- ordenação por atualização, menor preço ou maior preço, sempre com ID estável
  como desempate;
- cards levam ao detalhe USER da TASK-095.

## Fora de escopo

Nova coleta, tabela, migration, IA, comparação entre lojas, gráficos,
histórico detalhado, cupons, reviews textuais ou área administrativa.

## Validação mínima

- ownership e exclusão de `NO_MATCH` no statement;
- contrato resumido com última observação;
- renderização React dos dados principais e link de detalhe;
- Ruff, frontend lint/build e `git diff --check`.
