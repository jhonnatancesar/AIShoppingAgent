# TASK-040 — Criar confirmação de compra

Status: Concluída

## Objetivo

Criar o contrato de uma confirmação explícita e temporária para uma oferta
elegível escolhida pelo proprietário de uma missão ativa, sem executar compra.
A persistência foi integrada posteriormente pela TASK-041.

## Escopo

- Permitir solicitar confirmação para qualquer oferta elegível retornada pela
  comparação da TASK-039; a posição 1 continua sendo a recomendação da
  TASK-038, sem tratamento especial na confirmação.
- Vincular cada solicitação a `mission_id`, `offer_id`,
  `price_observation_id` e `owner_user_id`, além do snapshot completo exibível:
  produto, loja, vendedor opcional, URL, preço, frete, total, moeda,
  disponibilidade e horário da observação.
- Registrar `requested_at` e `expires_at` em UTC com TTL fixo de 15 minutos.
- Aceitar somente as decisões tipadas `confirm` e `cancel`.
- Impedir que usuário diferente do proprietário solicite ou resolva a
  confirmação.
- Antes de confirmar, recalcular a comparação pelas regras compartilhadas das
  TASKs 038 e 039 e exigir que a oferta ainda seja elegível e que os dados
  materiais de disponibilidade, moeda, preço, frete e total sejam equivalentes.
- Retornar `stale` quando a solicitação estiver expirada ou qualquer elemento
  material da evidência divergir. Novo UUID de observação com conteúdo
  equivalente não é mudança material. Uma nova
  solicitação baseada nas evidências atuais passa a ser obrigatória.
- Retornar `cancelled` para cancelamento explícito, inclusive após o TTL, e
  `confirmed` somente após toda a revalidação.

## Fora de escopo

- Evento e auditoria; a persistência e a idempotência durável foram adicionadas
  exclusivamente pela TASK-041.
- Compra, reserva, carrinho, checkout, redirecionamento externo ou qualquer
  ação financeira.
- Telegram, API HTTP, IA ou interpretação de texto livre.
- Alterar elegibilidade, recomendação ou comparação das TASKs 038 e 039.

## Critério de aceite

- Uma oferta elegível escolhida pelo proprietário gera uma solicitação
  imutável, completa e válida por 15 minutos.
- Outro usuário não consegue solicitar nem confirmar em nome do proprietário.
- `confirm` só produz `confirmed` quando a evidência material corrente é
  equivalente; nova observação idêntica continua válida e mudança relevante
  produz `stale`.
- Solicitação expirada nunca produz `confirmed` e exige nova solicitação.
- `cancel` produz `cancelled` sem consultar TTL ou oferta corrente.
- Ofertas inelegíveis não geram solicitação.
- Testes automatizados, pipeline completo e validação real em PostgreSQL 18
  aprovados; documentação e revisão técnica concluídas.

## Implementação e validação

- `app.purchase.confirmation` fornece contratos imutáveis, TTL fixo de 15
  minutos e os serviços `request_purchase_confirmation` e
  `resolve_purchase_confirmation`.
- A solicitação guarda `mission_id`, `offer_id`, `price_observation_id` e
  `owner_user_id`, além do snapshot monetário e comercial necessário à
  confirmação explícita.
- A resolução recalcula a comparação da TASK-039 dentro do TTL. Expiração,
  perda de elegibilidade ou divergência material resultam em `stale`; uma nova
  observação equivalente preserva a validade e a proveniência original.
- Testes automatizados cobrem confirmação, cancelamento, expiração, identidade,
  todos os campos relevantes e oferta inelegível.
- A TASK-041 corrigiu a semântica de observações equivalentes, cancelamento
  expirado e persistência, validando-as no PostgreSQL 18 real.
- Pipeline oficial aprovado com 461 testes e 94,45% de cobertura, sem
  migration ou dependência nova.

Próxima tarefa executável: TASK-045.
