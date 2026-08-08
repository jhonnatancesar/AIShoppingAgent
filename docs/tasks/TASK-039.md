# TASK-039 — Criar comparação de ofertas

Status: Concluída

## Objetivo

Criar uma comparação completa, ordenada e explicável das ofertas de uma missão
ativa, reutilizando sem divergência a elegibilidade e as evidências da
recomendação implementada na TASK-038.

## Escopo

- Chamar o fluxo da TASK-038 para obter o mesmo recorte de missão, fontes,
  coletas, observações correntes, moeda e motivos de inelegibilidade.
- Atribuir posição `1..N` somente às ofertas elegíveis, ordenadas por menor
  custo total, observação mais recente e UUID da oferta.
- Garantir que a posição 1 seja exatamente a recomendação da TASK-038 para o
  mesmo conjunto de dados, usando uma única regra compartilhada de ordenação.
- Manter ofertas inelegíveis sem posição e depois das elegíveis. Ordená-las
  somente por loja, produto e UUID, nunca por preço.
- Preservar todos os motivos de inelegibilidade: moeda da missão ausente,
  indisponibilidade, moeda incompatível e frete desconhecido.
- Em frete desconhecido, expor o preço do produto em `amount`, mas manter
  `total_amount=None`; nunca apresentar o produto como custo total.
- Informar produto, loja, vendedor opcional, URL, preço, frete, total quando
  determinável, moeda, disponibilidade, fulfillment, horários e evidências
  históricas da TASK-038.
- Retornar `insufficient_data` quando não houver oferta elegível, mantendo as
  evidências inelegíveis sem ranking.

## Fora de escopo

- Redefinir elegibilidade, evidência ou recomendação da TASK-038.
- Persistir comparações ou criar migration.
- IA, Telegram, API HTTP, eventos ou notificações.
- Compra, reserva, checkout, confirmação (TASK-040), trilha (TASK-041) ou
  qualquer ação financeira.

## Critério de aceite

- A comparação contém todas as ofertas do resultado da TASK-038 exatamente uma
  vez; somente elegíveis recebem posição consecutiva.
- A posição 1 e a recomendação da TASK-038 são invariavelmente a mesma oferta.
- Frete desconhecido gera exclusão clara e `total_amount=None`.
- Inelegíveis ficam depois das elegíveis e sua ordenação não depende de preço.
- Ausência de elegíveis produz `insufficient_data` sem ranking inventado.
- Testes automatizados, pipeline completo e validação real em PostgreSQL 18
  aprovados; documentação e revisão técnica concluídas.

## Implementação e validação

- `app.purchase.comparison` produz o ranking somente leitura e reutiliza
  `recommend_for_mission` e `rank_eligible_evidence`, a mesma ordenação usada
  pela TASK-038.
- Testes automatizados cobrem a invariância da primeira posição, frete
  desconhecido com `total_amount=None`, inelegíveis fora do ranking e ordenação
  de inelegíveis independente de preço.
- Validação real no PostgreSQL 18 confirmou ranking, evidências históricas,
  `insufficient_data` e rollback sem resíduos.
- Pipeline oficial aprovado com 444 testes e 94,81% de cobertura, sem migration
  ou dependência nova.

Próxima tarefa executável: TASK-040.
