# TASK-090 — Pausar/retomar manual e cancelamento em massa de missões

Status: **Implementada e testada (2026-08-21), aguardando revisão do
usuário antes de commit/push/deploy.**

## Origem

Usuário relatou 5 queixas reais de uso do bot em produção. Esta TASK
resolve somente as 3 primeiras:

1. Editar uma missão que estava `ACTIVE` a deixa `PAUSED` (pré-condição
   do domínio para editar critérios) sem nenhuma forma de voltar a
   `ACTIVE`.
2. Não existe comando manual dedicado para pausar nem para retomar uma
   missão.
3. `/cancelar_missao` só aceitava cancelar uma missão por vez.

As outras duas queixas (alerta de preço-alvo repetindo mesmo sem queda;
busca "iphone 16 512" não encontrando oferta real da Amazon) ficam
**fora de escopo** desta TASK — ver "Fora de escopo" abaixo.

## Regra inegociável

`/cancelar_missao`, `/pausar` e `/retomar` são 100% determinísticos:
nenhum dos três nunca chama `IntentInterpreter` nem qualquer provider de
IA (Gemini, Groq, OpenRouter) para interpretar comando, seleção numérica
ou confirmação. Comprovado por teste com um `_FakeAdapter` que levanta
`AssertionError` se for chamado ("poison pill"), mais `assert
adapter.calls == []` explícito em cada teste novo.

## Implementação

### `/pausar` e `/retomar` (novos comandos)

`backend/app/telegram/router.py`: dois comandos novos, cada um consulta
as missões do próprio usuário no status relevante
(`_query_missions_by_status`, já existente, reaproveitada sem alteração
de assinatura) — `/pausar` lista `ACTIVE`, `/retomar` lista `PAUSED` — e
delega para um helper local novo, `_start_manual_command_flow`, que
também passou a ser usado por `/cancelar_missao` (ver abaixo). Registrados
em `backend/scripts/register_telegram_commands.py` e no menu de `/ajuda`.

### `/cancelar_missao` (agora aceita seleção múltipla)

Antes: só aceitava exatamente uma missão por vez (`kind:
"cancel_mission_choice"`, `_apply_cancel_mission_choice`, código próprio
e paralelo à infraestrutura genérica que já existia desde a TASK-085).

Depois: `_start_cancel_mission_flow` passou a usar o mesmo
`_start_manual_command_flow` de `/pausar`/`/retomar`, que por sua vez
reaproveita integralmente a infraestrutura genérica já existente da
TASK-085 (`stage_mission_command`/`describe_mission_command` para
candidata única; `stage_mission_command_choice` +
`describe_mission_command_choice_prompt` +
`_apply_mission_command_choice` para várias candidatas, usando
`parse_multi_numbered_choice`). O código próprio antigo
(`_CANCEL_MISSION_CHOICE`, `_apply_cancel_mission_choice`) foi removido —
não ficou código morto nem duplicado. Nenhuma máquina de estados
nova foi criada; a TASK-085 já cobria exatamente esse formato genérico
por `MissionCommand`, só não estava conectada aos três comandos manuais.

`parse_multi_numbered_choice` (`backend/app/telegram/confirmation.py`,
sem alteração) aceita `"1"`, `"1,3"`, `"2, 4, 5"` (espaços tolerados),
deduplica preservando a primeira ocorrência (`"1,1,3"` vira `[1, 3]`) e
invalida a resposta inteira — nunca execução parcial — se qualquer token
não for um número válido dentro do intervalo listado.

### `/editar_missao` — pausa automática não retoma sozinha

Auditoria confirmada: o fluxo de edição nunca reativa uma missão
sozinho, em nenhum dos dois casos abaixo. A distinção exigida pelo
usuário ("estava ativa antes da edição" vs. "já estava pausada antes da
edição") é rastreada por um booleano novo, `auto_paused`, propagado por
todo o `pending_intent` da edição (`stage_edit_mission`,
`_stage_edit_menu` e todos os sub-estados intermediários de lojas/preço)
— a "solução mínima coerente" pedida, sem nova tabela nem máquina de
estados: é só mais um campo no mesmo dicionário JSON que já existia.

Mudança de comportamento (fora do escopo de bug puro, sinalizada
deliberadamente): antes, pausar uma missão `ACTIVE` para editar
terminava numa mensagem pedindo para reenviar `/editar_missao`; agora o
fluxo encena o próximo estado (`await_edit_menu_choice`) e o menu
principal já aparece na mesma resposta, sem exigir um segundo comando.
Quando a missão já estava `PAUSED`, nada muda nesse ponto — vai direto
ao menu como sempre foi.

A mensagem final de `/editar_missao` sempre orienta usar `/retomar`
(comando real, criado por esta mesma TASK) e o texto varia só pela
origem da pausa:
- `auto_paused=True`: "A missão continua pausada.\n\nUse /retomar quando
  quiser voltar a monitorar."
- `auto_paused=False`: "A missão continua pausada, como já estava antes
  desta edição.\n\nUse /retomar quando quiser voltar a monitorar."

### Roteamento sem IA

`_resolve_pending_intent` já resolvia `kind`s determinísticos antes de
qualquer fallback de IA (padrão preexistente, não alterado). Um bug real
foi encontrado e corrigido durante a implementação: o executor da pausa
para edição (`_execute_pause_for_edit`) passou a encadear diretamente no
menu de edição substituindo o `pending_intent` por um novo estado, mas o
chamador limpava `user.pending_intent = None` incondicionalmente logo
depois — o que apagaria esse novo estado antes mesmo da resposta ser
enviada. Corrigido para só limpar quando o executor não substituiu o
`pending_intent` (`if user.pending_intent is payload: user.pending_intent
= None`).

### Coleta

Nenhum código novo foi necessário para pausar/retomar afetarem
coleta: a elegibilidade de novas coletas já filtra estritamente por
`Mission.status == ACTIVE` (`backend/app/collection/orchestration.py`,
`backend/app/missions/schedule.py`), confirmado por leitura direta do
código antes de qualquer alteração. `transition_mission_async(command=
PAUSE)`/`(command=RESUME)` (já existente, `TRANSITIONS` table em
`backend/app/missions/service.py`) é suficiente sozinho; nenhuma coleta
extra é disparada ao retomar. `MissionSchedule.is_enabled` continua
sendo desativado só por `CANCEL` (comportamento preexistente, não
estendido a `PAUSE` — desnecessário pelo motivo acima).

Cancelamento preserva a semântica atual: status lógico
(`MissionStatus.CANCELLED`), nunca exclusão física; histórico
(`MissionTransition`, append-only) permanece intacto.

## Testes

`tests/test_telegram_router.py` e `tests/test_telegram_confirmation.py`,
**214 testes**, incluindo os novos:

- 7 testes de `/pausar`/`/retomar`: uma missão, várias (com espaços na
  seleção, prova de tolerância de formato), seleção inválida, nenhuma
  missão elegível — todos com `_FakeAdapter` poison-pill e `assert
  adapter.calls == []`.
- `/cancelar_missao`: execução imediata sem IA (reescrito), seleção
  múltipla com duplicata proposital `"1,1,3"` (prova de deduplicação —
  só 2 transições, não 3), seleção inválida mantendo estado, isolamento
  entre usuários (pending_intent aponta para missão de outro dono →
  "não encontrada", zero chamadas de IA).
- `/editar_missao`: pausa-para-edição agora encadeia direto no menu
  (reescrito), mais um teste novo parametrizado
  (`test_execute_edit_mission_message_reflects_auto_paused_origin`) que
  roda o executor duas vezes (`auto_paused=True`/`False`) e prova o
  texto final de cada variante, incluindo que `/retomar` (comando real)
  sempre aparece.
- `test_registered_telegram_commands_use_only_bot_api_compatible_names`:
  atualizado para exigir `pausar` e `retomar` no menu registrado.

### Auditoria adicional (2026-08-21, segunda rodada)

O usuário pediu fechamento de 4 pontos antes de aprovar, cada um com
prova concreta, não só afirmação:

1. **Seleção parcial inválida sem execução parcial** — 3 testes novos
   (`test_cancel_mission_partial_invalid_selection_rejects_entire_batch`,
   `test_pause_command_partial_invalid_selection_rejects_entire_batch`,
   `test_resume_command_partial_invalid_selection_rejects_entire_batch`):
   3 candidatas, seleção `"1,3,99"` (99 inválido) para cada um dos três
   comandos — `transition_mission_async` mockado para levantar
   `AssertionError` se for chamado, provando que NENHUMA das duas
   posições válidas (1 e 3) executa parcialmente; estado permanece
   idêntico ao anterior à resposta; zero chamadas de IA.
2. **Ciclo completo de edição, cenário A** (`test_full_cycle_active_pause_edit_resume_returns_to_active`):
   uma única missão `SimpleNamespace` mutável simula o ciclo real
   `ACTIVE → /editar_missao → pausa real (transição oficial) → menu →
   Preço-alvo → edição confirmada → PAUSED → /retomar → transição
   oficial → ACTIVE` em 7 turnos encadeados via `_handle_message`,
   reaproveitando o mesmo `user`/`session` a cada turno (como uma
   conversa real). Cada turno verifica o status real da missão, o
   `auto_paused` correto (`True` só no trecho provocado pela própria
   edição), a versão otimista repassada a cada `transition_mission_async`
   (`expected_state_version == mission.state_version` no momento da
   chamada), o texto final exato, e termina com `mission.status ==
   MissionStatus.ACTIVE` — a mesma condição que
   `collection/orchestration.py` e `missions/schedule.py` exigem para
   elegibilidade de coleta (confirmado por leitura de código, não só
   suposição). `adapter.calls == []` ao final dos 7 turnos.
3. **Ciclo completo de edição, cenário B** (`test_full_cycle_paused_edit_stays_paused_without_auto_resume`):
   missão já `PAUSED` → `/editar_missao` → edição de preço-alvo
   concluída → `transition_mission_async` mockado para levantar
   `AssertionError` se chamado (prova de que pausar/retomar realmente
   NUNCA é acionado nesse caminho) → `auto_paused` fica `False` em cada
   etapa → mensagem final contém "como já estava antes desta edição" e
   **não** contém o fragmento exclusivo da variante `auto_paused=True`
   (`"pausada.\n\nUse /retomar"`) → `mission.status` permanece `PAUSED`
   → zero IA.
4. **Prova de regressão do fix de `pending_intent`** — dois testes
   diretos sobre `_resolve_pending_intent`
   (`test_resolve_pending_intent_preserves_new_state_set_by_executor` e
   sua contraparte `..._clears_state_when_executor_does_not_replace_it`).
   O primeiro foi **verificado manualmente contra a implementação
   anterior ao fix**: revertendo temporariamente a linha `if
   user.pending_intent is payload: user.pending_intent = None` de volta
   para o `user.pending_intent = None` incondicional antigo,
   `pytest -k resolve_pending_intent_preserves` **falha** (`assert None
   == {...}`) — e, como efeito colateral esperado, o cenário A completo
   (item 2) também falha contra o código antigo, por exercitar o mesmo
   bug. A correção foi restaurada antes de qualquer commit e a suíte
   completa voltou a **214 passed**; `git diff` do arquivo confirmado
   idêntico ao estado pré-reversão (nenhum resíduo do experimento).

### Causa real das 31 falhas da rodada anterior — classificação B

O relatório anterior desta TASK citou "31 falhas" numa mesma rodada que
também dizia "suíte verde", uma contradição que o usuário corretamente
recusou aceitar sem explicação. Investigação controlada:

- As 31 falhas (`test_playwright_browser.py::test_chromium_opens_local_page_and_closes_session`
  e 30 casos em `test_store_provider_installments.py`/`test_store_providers.py`
  que abrem `BrowserSession`/Chromium real) são **idênticas, byte a
  byte**, com e sem as alterações desta TASK — comprovado rodando a
  mesma suíte duas vezes com `git stash` isolando exatamente as 10
  alterações de código desta TASK e comparando as duas listas de nomes
  de teste (`diff` vazio). **Não é regressão desta TASK.**
- **Causa raiz real, não só "isolamento"**: `pytest.ini`/`pyproject.toml`
  define `testpaths = ["tests"]`. A rodada anterior invocou
  `pytest tests/ --ignore=tests/integration ...`, passando `tests/`
  como argumento posicional explícito. Mesmo com `--ignore`, o pytest
  ainda importa `tests/integration/conftest.py` durante a fase de
  coleta quando o caminho pai é passado explicitamente na linha de
  comando (confirmado com `--collect-only`: o import aparece nos
  warnings mesmo com `--ignore=tests/integration`). Esse `conftest.py`
  tem, fora de qualquer fixture, no nível do módulo:
  `if sys.platform == "win32": asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())`
  (linha 27, necessário para o psycopg assíncrono dos testes de
  integração). Essa chamada é **global e irreversível para o processo**
  — uma vez executada, todo teste subsequente na mesma sessão do pytest
  herda `WindowsSelectorEventLoopPolicy`, cujo `SelectorEventLoop` **não
  implementa subprocessos no Windows**
  (`asyncio.base_events._make_subprocess_transport` levanta
  `NotImplementedError`, limitação documentada do próprio `asyncio` —
  só `ProactorEventLoop` cria subprocessos no Windows). O Playwright
  precisa abrir um subprocesso Node para falar com o Chromium; com a
  policy trocada, essa chamada falha com `NotImplementedError` para
  todo teste que abre `BrowserSession` real, não só
  `test_playwright_browser.py`.
- **Por que rodadas anteriores (TASK-089, por exemplo) apareciam
  verdes**: o procedimento documentado do projeto nunca passa `tests/`
  como argumento posicional — usa só
  `pytest --ignore=tests/integration --ignore=tests/e2e
  --cov=app --cov-fail-under=90` (testpaths supre o caminho
  implicitamente), invocação sob a qual `tests/integration/conftest.py`
  **nunca é importado** (confirmado com `--collect-only`: nenhum
  warning de `WindowsSelectorEventLoopPolicy` aparece). A "suíte verde"
  historicamente reportada sempre usou essa invocação correta; a
  contradição do relatório anterior veio de eu ter adicionado `tests/`
  na linha de comando por conta própria, sem perceber esse efeito
  colateral do pytest.
- **Prova final**: repetindo a suíte completa com a invocação correta
  do projeto (sem `tests/` posicional), com as alterações desta TASK
  aplicadas → **1286 passed, 1 skipped, 90,21% cobertura, 0 falhas**.
  Nenhum arquivo de loja/Playwright foi tocado para chegar nesse
  resultado — a mudança foi só na forma de invocar o pytest, não no
  código-fonte nem nos testes.

**Classificação: B — problema de isolamento/invocação da suíte de
testes.** As 31 falhas realmente não foram causadas pela TASK-090 (lista
idêntica com e sem seu código), mas a causa comprovada não é "só
pré-existente e independente" no sentido de um problema do produto: é um
problema de isolamento entre a suíte de integração e a suíte
não-integração, disparado pela forma incorreta de invocar o pytest
(`tests/` posicional junto de `--ignore=tests/integration`). Essa
invocação faz `tests/integration/conftest.py` ser importado mesmo
ignorado; seu código de módulo troca a política global de event loop
para `WindowsSelectorEventLoopPolicy`, que contamina o processo inteiro
e não suporta os subprocessos que o Playwright precisa abrir — daí as 31
falhas em testes que abrem `BrowserSession`/Chromium real. Usando a
invocação oficial/documentada do projeto (sem `tests/` posicional), o
resultado é **1286 passed, 1 skipped, 0 failed, 90,21% cobertura**.
Nenhum código de produção, provider de loja ou teste precisou mudar —
a correção foi só na forma de invocar o pytest.

Resultados finais desta rodada de auditoria (todos com o código de
TASK-090 aplicado):

- **Resultado funcional da TASK-090**:
  `pytest tests/test_telegram_router.py tests/test_telegram_confirmation.py
  --no-cov` → **214 passed** (207 anteriores + 7 novos desta auditoria).
- `pytest tests/test_missions.py tests/test_collection_orchestration.py
  --no-cov` (status/transição de missão) → **59 passed**.
- **Resultado global da suíte** (invocação correta do projeto, sem
  `tests/` posicional):
  `pytest --ignore=tests/integration --ignore=tests/e2e
  -m "not integration and not e2e"` → **1286 passed, 1 skipped, 90,21%
  cobertura, 0 falhas**.
- `python scripts/run_integration_tests.py tests/integration/test_mission_edit.py
  tests/integration/test_collection_orchestration.py` (únicas integrações
  reais tocando `MissionStatus`/`PAUSED`/coleta) → **18 passed** em
  PostgreSQL 18.4-alpine descartável.
- `ruff check` nos 5 arquivos de código alterados e no repositório
  inteiro (`ruff check .`) → limpo nos dois casos.
- `git diff --check` → limpo.

## Fora de escopo (mantido pendente, não tocado)

- Alerta de preço-alvo notificando repetidamente mesmo sem queda —
  `backend/app/alerts/evaluator.py` não foi aberto para edição.
- Busca "iphone 16 512" não encontrando oferta real da Amazon —
  `backend/app/collection/model_matching.py` não foi aberto para edição.

Ambos permanecem registrados como pendentes, para uma TASK futura
dedicada a cada um.

## Documentação atualizada

- `docs/architecture/telegram.md`: contagem de comandos, descrição de
  `/cancelar_missao`/`/pausar`/`/retomar` e do novo comportamento de
  `/editar_missao`.
- `docs/architecture/mission-commands.md`: seção de despacho por
  `IntentKind` (cancelamento/pausa/retomada compartilhados, edição
  encadeando no menu), nomes formais na Bot API.
- Este documento (`docs/tasks/TASK-090.md`).

## Restrições desta implementação

Autorizada para implementação e teste completos, mas **sem commit, push
ou redeploy** — aguardando revisão explícita do usuário.
