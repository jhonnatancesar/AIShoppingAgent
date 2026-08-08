# TASK-038 — Definir fluxo de recomendação

Status: Concluída em 2026-08-08

## Objetivo

Implementar uma recomendação determinística para uma missão ativa a partir das
observações históricas persistidas pela própria missão, retornando uma única
oferta quando o custo total for determinável e houver moeda compatível.

## Escopo

- Considerar somente coletas bem-sucedidas vinculadas à missão e somente lojas
  presentes em `mission_sources`.
- Usar a observação mais recente de cada oferta como estado corrente; somente
  `available` participa da seleção.
- Exigir moeda igual a `MissionCriteria.target_currency`. Não converter,
  comparar nem somar moedas diferentes. Uma missão sem moeda definida não
  possui dados suficientes para recomendação determinística.
- Exigir `shipping_amount` conhecido. Frete nulo nunca significa zero ou grátis
  e torna a oferta inelegível para vencer pelo menor custo total.
- Escolher o menor `total_amount`; empates usam a observação mais recente e, por
  fim, o UUID da oferta, garantindo resultado estável.
- Devolver evidências de todas as ofertas encontradas no recorte da missão,
  inclusive as inelegíveis, sem transformá-las em uma comparação ordenada.
- Informar produto, loja, vendedor opcional, URL, valores monetários,
  disponibilidade, fulfillment, horários e os identificadores do histórico
  usado. O vendedor permanece nulo para lojas diretas sem entidade separada.
- Retornar explicitamente `insufficient_data` e uma razão estável quando não
  houver moeda da missão, observações, disponibilidade, moeda compatível,
  frete conhecido ou oferta elegível.

## Fora de escopo

- Comparação completa e ordenada de ofertas (TASK-039).
- Compra, reserva, checkout ou qualquer ação financeira.
- Confirmação de compra (TASK-040) e trilha de compra (TASK-041).
- IA, Telegram, API HTTP, eventos, notificações ou persistência de uma
  recomendação.

## Critério de aceite

- Uma missão ativa com ofertas elegíveis recebe exatamente uma recomendação
  determinística de menor custo total na moeda do critério.
- Frete desconhecido, moeda diferente e indisponibilidade impedem uma oferta de
  vencer, mas permanecem explicados nas evidências disponíveis.
- O histórico usado é identificável e o vendedor é evidência opcional.
- Ausência de dados elegíveis produz `insufficient_data`, nunca uma escolha
  parcial ou conversão implícita.
- Testes automatizados, pipeline completo e validação real em PostgreSQL 18
  aprovados; documentação e revisão técnica concluídas.

## Implementação e validação

- `app.purchase` contém contratos imutáveis, razões de insuficiência e exclusão
  e o serviço `recommend_for_mission`.
- As evidências permanecem em ordem estável por UUID, sem antecipar a ordenação
  comercial da TASK-039.
- `backend/scripts/validate_recommendation_flow.py` validou em PostgreSQL 18
  real menor total elegível, frete desconhecido, moeda incompatível, fonte não
  selecionada, vendedor opcional, histórico anterior/mínimo e o caso sem total
  determinável; a transação foi revertida e a ausência de resíduos confirmada.
- Pipeline completo aprovado em Python 3.14.6: 440 testes, 95,09% de cobertura,
  Ruff, Alembic com head único e Docker Compose válidos. Nenhuma migration ou
  dependência nova foi criada.

## Próxima tarefa no fluxo

TASK-039 — Criar comparação de ofertas.
