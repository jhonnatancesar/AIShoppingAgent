# TASK-070 — Perguntar lojas por lista numerada quando a missão for criada sem nenhuma

Status: **Concluída em 2026-08-11**, desenho aprovado explicitamente pelo
usuário (com ajustes obrigatórios sobre ordem, validação estrita e
preservação do `_DEFAULT_V1_SOURCE_CODES` do service), implementada e
validada com pipeline oficial.

Dependência: nenhuma direta. Item 7 da `v1.0.2` (`docs/internal/v1.0.2-scope.md`,
`DEC-060`), registrado depois da TASK-069 concluir o planejamento
original de 5 itens. Reaproveita o padrão de confirmação da TASK-058 e o
precedente de lista numerada da TASK-067 (`/cadastro`), sem alterá-los.

## Contexto

Antes desta TASK, quando um `Intent` de `CREATE_MISSION` chegava sem
nenhuma loja em `IntentParameters.sources`, o webhook assumia
silenciosamente as quatro fontes da V1 (Pichau, Terabyte, Amazon, Kabum)
— o usuário só via essa escolha automática na mensagem de confirmação
("🏪 Lojas: todas as lojas disponíveis"), sem ter sido perguntado. O
usuário pediu para trocar isso por uma pergunta explícita, por lista
numerada, no mesmo estilo já usado no `/cadastro`.

## Auditoria (2026-08-11)

1. **Onde o default acontecia**: não era no webhook — `_stage_create_mission`
   (`router.py`) e `stage_create_mission`/`describe_create_mission`
   (`confirmation.py`) só passavam `sources` adiante, vazio ou não. O
   default de fato só existia em `create_mission_from_criteria`
   (`backend/app/missions/service.py`), com uma constante
   `_DEFAULT_V1_SOURCE_CODES` aplicada só na execução, depois da
   confirmação.
2. **Outros chamadores dependem do default do service**: confirmado por
   busca de todos os usos de `create_mission_from_criteria` —
   `backend/scripts/validate_collection_worker.py` e
   `tests/test_mission_creation.py` chamam o serviço diretamente com
   `source_codes=()`, esperando o preenchimento automático. Por isso
   `_DEFAULT_V1_SOURCE_CODES` **não foi removido nem alterado** — a
   TASK-070 muda só o fluxo do Telegram, que agora nunca mais chega ao
   serviço com `source_codes` vazio.
3. **Padrão numerado do `/cadastro`**: `_parse_stores`
   (`backend/app/users/registration.py`) usa outra ordem (1 Kabum/2
   Pichau/3 Terabyte/4 Amazon) e é **permissivo** — aceita a parte
   reconhecida da resposta e ignora tokens inválidos silenciosamente.
   Essa permissividade não atende ao pedido desta TASK (validação
   completa: `"1,9"` deve ser rejeitado, não virar só `"1"`), então não
   foi reaproveitado; um parser novo, estrito, foi escrito.
4. **Nenhum mecanismo existente encaixava**: `registration_step` é uma
   FSM fechada nos 4 passos do `/cadastro`; `user.pending_intent`
   (TASK-058) sempre resolvia a resposta seguinte como confirmar/cancelar
   via IA (`resolve_answer`), nunca como uma lista de valores. Um novo
   estado pendente (`await_create_mission_sources`) e um novo ramo em
   `_resolve_pending_intent` — que decide **antes** de chamar
   `resolve_answer` — foram necessários.

## Desenho aprovado (2026-08-11)

- **Ordem própria desta TASK**: `1 Pichau, 2 Terabyte, 3 Amazon, 4 Kabum,
  5 Todas` — diferente da ordem do `/cadastro`, que não foi alterada.
- **Validação completa, sem aceitação parcial**: qualquer token fora do
  mapa invalida a resposta inteira (`"1,9"` → inválido, mesmo o `"1"`
  existindo). Repetição é deduplicada (`"1,1"` → só a loja 1). Misturar
  `"5"` com qualquer outro número ainda resulta em todas (`"5,1"` →
  todas). Texto sem opção reconhecível é inválido.
- **Fluxo**: `CREATE_MISSION` sem `sources` não cria a missão — encena
  `await_create_mission_sources` (preservando `search_query`/
  `target_amount`/`target_currency`), envia a lista numerada, e a
  resposta seguinte é interpretada de forma determinística (sem IA,
  nunca passa pelo `IntentInterpreter` de novo). Resposta inválida
  mantém o mesmo estado pendente e repete o pedido. Resposta válida
  preenche `sources` e só então encena a confirmação normal sim/não já
  existente (TASK-058); a missão só é criada depois dessa confirmação.
- **`_DEFAULT_V1_SOURCE_CODES` preservado**: o service continua com o
  fallback, usado hoje por `validate_collection_worker.py` e por um
  teste unitário — nenhuma mudança de contrato do service para esta
  TASK.

## Implementação (2026-08-11)

- **`backend/app/telegram/confirmation.py`**: `parse_numbered_store_selection`
  (genérico/configurável — `option_map`/`all_tokens` por parâmetro, sem
  ordem fixa embutida; validação completa, sem aceitação parcial);
  `_CREATE_MISSION_SOURCE_OPTIONS`/`_CREATE_MISSION_SOURCE_ALL_TOKENS`
  (mapa e ordem próprios desta TASK); `resolve_create_mission_sources`
  (aplica o mapa acima); `stage_await_create_mission_sources`
  (`"kind": "await_create_mission_sources"`, guarda `search_query`/
  `target_amount`/`target_currency`); `describe_create_mission_sources_prompt`/
  `describe_create_mission_sources_retry` (mensagens fixas, sem IA).
- **`backend/app/telegram/router.py`**: `_stage_create_mission` ganha o
  ramo "sem `sources`" descrito acima; `_PENDING_INTENT_PERMISSIONS`
  ganhou `"await_create_mission_sources": Permission.MISSION_CREATE`;
  `_resolve_pending_intent` decide pelo `kind` **antes** de chamar
  `resolve_answer`, desviando para a nova
  `_apply_create_mission_sources_answer` quando pendente é
  `await_create_mission_sources`.
- **Nenhuma mudança** em `backend/app/missions/service.py`,
  `backend/app/users/registration.py` (`/cadastro` intocado),
  providers, coleta, ranking, alertas, TASK-068 ou TASK-069.
- **Nenhuma migration**: nenhum campo novo de banco.

## Validação (2026-08-11)

- **Pipeline oficial completo** (`scripts\check.ps1`): Gitleaks, lint,
  formatação, testes (cobertura ≥ 90%), migration head sem alteração —
  todos aprovados.
- **Testes novos de parser/confirmação**
  (`tests/test_telegram_confirmation.py`): uma loja, múltiplas lojas,
  "todas", "todas" misturado com outro número, opção inválida, mistura
  válida+inválida (nunca aceita parcialmente), repetição de opção
  (deduplicada), preservação dos critérios ao encenar
  `await_create_mission_sources`, e a ordem/mapa específicos desta TASK
  (`1 Pichau/2 Terabyte/3 Amazon/4 Kabum/5 Todas`, distinta do
  `/cadastro`).
- **Testes novos de fluxo** (`tests/test_telegram_router.py`):
  `CREATE_MISSION` sem lojas encena a pergunta sem criar nada e
  preserva os demais critérios; resposta válida avança para a
  confirmação normal sem chamar o `IntentInterpreter` de novo; resposta
  inválida mantém o mesmo estado pendente e repete o pedido; um teste de
  ponta a ponta (3 mensagens sequenciais) confirma que a missão só é
  criada depois da seleção válida **e** da confirmação sim/não, com o
  `IntentInterpreter` chamado exatamente uma vez em toda a sequência.
- **`_DEFAULT_V1_SOURCE_CODES` intocado**: confirmado que
  `backend/scripts/validate_collection_worker.py` e um teste unitário de
  `create_mission_from_criteria` continuam dependendo dele; nenhuma
  mudança de contrato do service foi feita.
- **Produção**: nenhum comando executado contra o servidor real da
  `v1.0.1`; nenhuma tag `v1.0.2` criada.

## Escopo confirmado

1. `CREATE_MISSION` sem `sources` pergunta lojas por lista numerada
   (`1 Pichau/2 Terabyte/3 Amazon/4 Kabum/5 Todas`) em vez de assumir as
   quatro automaticamente.
2. Novo estado pendente `await_create_mission_sources`, resolvido de
   forma determinística (sem IA), nunca passando pelo
   `IntentInterpreter` de novo nem criando uma segunda missão.
3. Validação completa da resposta — qualquer token não reconhecido
   invalida a resposta inteira.
4. Só chega à confirmação sim/não normal (TASK-058) depois de uma
   seleção válida.
5. `/cadastro` (TASK-067) intocado; `_DEFAULT_V1_SOURCE_CODES` do
   service preservado por depender de outros chamadores.
6. Providers, coleta, ranking, alertas, TASK-068 e TASK-069 intocados;
   produção intocada; nenhuma tag `v1.0.2`.

## Fora do escopo desta TASK

- Uniformizar a ordem numerada entre este fluxo e o `/cadastro`.
- Qualquer IA nova (a interpretação da lista numerada é 100%
  determinística).
- Alterar `_DEFAULT_V1_SOURCE_CODES` ou o contrato de
  `create_mission_from_criteria`.

## Encerramento

Concluída em 2026-08-11. Uma `CREATE_MISSION` sem loja nenhuma informada
agora pergunta explicitamente, por lista numerada própria (`1 Pichau/2
Terabyte/3 Amazon/4 Kabum/5 Todas`), em vez de assumir as quatro fontes
da V1 silenciosamente. A resposta é interpretada de forma determinística,
validando a entrada por completo (nunca aceita parcialmente), e só avança
para a confirmação sim/não já existente depois de uma escolha válida — a
missão nunca é criada antes disso. `/cadastro` e o `_DEFAULT_V1_SOURCE_CODES`
do service permanecem intocados. Com esta TASK, os itens 1 a 5 (planejamento
original) e o item 7 da `v1.0.2` estão concluídos; o item 6 (bloquear
`/cadastro` para usuário já autenticado) continua registrado e pendente,
sem TASK aberta. Produção da `v1.0.1` intocada; nenhuma tag `v1.0.2`
criada.
