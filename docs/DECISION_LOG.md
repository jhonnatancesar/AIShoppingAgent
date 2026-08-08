# Decision Log

Este arquivo registra decisões arquiteturais e funcionais tomadas durante o desenvolvimento. Ele preserva o motivo de cada escolha e direciona a atualização documental necessária, sem substituir os ADRs para decisões arquiteturais formais.

## Processo obrigatório para novas funcionalidades

Antes de implementar qualquer funcionalidade sugerida, analisar seu impacto no escopo, nas dependências, na arquitetura, nos dados, na operação e na complexidade do projeto. A ideia deve receber exatamente uma das classificações abaixo:

- Implementar agora
- Nova TASK do MVP
- Backlog
- Versão futura
- Out of Scope
- Rejeitada

Após a classificação, registrar a decisão neste arquivo e atualizar a documentação correspondente. A classificação não autoriza implementação fora de uma TASK explicitamente solicitada.

## Formato de registro

### DEC-AAA — Título da decisão

- **Data:** AAAA-MM-DD
- **Ideia:** descrição objetiva da proposta ou decisão.
- **Classificação:** uma das seis classificações permitidas.
- **Justificativa:** impacto avaliado e motivo da classificação.
- **Próxima ação:** documento a atualizar, TASK a criar quando aplicável, ou ação de não implementação.

## Registros

### DEC-014 — Criar a TASK-057 para melhorar a robustez da interpretação de intenção

- **Data:** 2026-08-08
- **Ideia:** durante a validação manual real da TASK-035, uma mensagem real
  do usuário foi classificada como `unknown` quando, na avaliação do
  usuário, deveria ter sido reconhecida — o `IntentInterpreter` (TASK-032)
  precisa ficar mais robusto para diferentes formas de escrita.
- **Classificação:** Nova TASK do MVP
- **Justificativa:** o usuário pediu explicitamente o registro como próxima
  tarefa, não a correção imediata. O `IntentInterpreter` já foi validado
  contra o Gemini real nas TASKs 032, 034 e 035 para as mensagens testadas;
  isso é um refinamento de qualidade de classificação, não um defeito
  estrutural, e não deve ser implementado sem uma TASK própria — alterar o
  prompt de sistema durante a validação da TASK-035 misturaria escopos e
  arriscaria regressão sem a validação dedicada que a mudança merece.
- **Próxima ação:** criada `docs/tasks/TASK-057.md`, registrada no roadmap;
  aguarda solicitação explícita para ser executada.

### DEC-013 — Distinguir erro conhecido de falha inesperada no despacho de missão do webhook

- **Data:** 2026-08-08
- **Ideia:** ao capturar exceções no despacho de `Intent` por comando de
  missão (TASK-035), a rota do webhook só deve tratar como resposta
  controlada (`204` + mensagem ao usuário) os erros **esperados e conhecidos**
  de domínio/validação. Qualquer falha inesperada — incluindo `IntegrityError`
  residual não tratada no serviço apropriado — não deve ser mascarada.
- **Classificação:** Implementar agora
- **Justificativa:** decisão do usuário ao revisar o plano da TASK-035: uma
  captura genérica de exceções esconderia bugs reais atrás de um `204`
  aparentemente saudável. A única corrida esperada com `IntegrityError`
  continua isolada dentro de `get_or_create_telegram_user` (TASK-056, via
  `SAVEPOINT`); tudo o mais que chegar até a rota como `IntegrityError` é
  inesperado e deve subir como `500`. A lista de exceções conhecidas é
  fechada e explícita: `MissionNotFoundError`, `MissionVersionConflictError`,
  `InvalidMissionTransitionError`, `MissionTransitionConditionError`,
  `MissionReferenceError` e `MissionIntentError`.
- **Próxima ação:** nenhuma; documentado em `docs/MISSION_COMMANDS.md` e
  implementado em `backend/app/telegram/router.py`
  (`_KNOWN_DISPATCH_ERRORS`).

### DEC-012 — Fechar o escopo da TASK-035 (comandos de missão via Telegram)

- **Data:** 2026-08-08
- **Ideia:** `docs/tasks/TASK-035.md` só trazia uma frase de escopo real
  (seleção de fontes). Quatro decisões precisaram ser fechadas antes de
  implementar: (1) sem teclado interativo agora — as fontes vêm do que o
  `IntentInterpreter` já extrai do texto livre; (2) a TASK-035 envia uma
  resposta síncrona mínima ao Telegram, e não a TASK-036; (3) o seed das
  quatro lojas da V1 entra nesta TASK, por ser dado de referência fixo já
  definido em `docs/MARKETPLACE_SOURCES.md`; (4) quando o `Intent` de criação
  não especifica nenhuma fonte, a missão usa automaticamente as quatro
  fontes da V1 e sai `active` — nunca fica em `draft` por falta de fonte.
- **Classificação:** Implementar agora
- **Justificativa:** `docs/MVP.md` exige que "um usuário autorizado consegue
  criar e consultar uma missão pelo canal Telegram" — sem resposta síncrona,
  "consultar" não tem como funcionar para o usuário. `TASK-036` continua
  reservada a notificações proativas orientadas a evento (alertas de preço,
  TASK-027/042-044), não a essa resposta ao próprio comando do usuário. O
  seed de lojas é dado de referência fixo, sem decisão de domínio nova,
  diferente do que justificou uma TASK própria para a identidade do Telegram
  (`DEC-011`). Toda `CREATE_MISSION` válida sair `active` evita o estado
  intermediário "criada mas inerte" que uma missão em `draft` sem fonte
  representaria.
- **Próxima ação:** nenhuma; documentado em `docs/MISSION_COMMANDS.md` e
  `docs/tasks/TASK-035.md`.

### DEC-011 — Criar a TASK-056 para vincular identidade do usuário ao Telegram antes da TASK-035

- **Data:** 2026-08-07
- **Ideia:** ao preparar a TASK-035 ("Criar comandos de missão"), identifiquei
  que persistir uma missão via Telegram exige `Mission.user_id`, uma FK
  obrigatória para `User`. `docs/USERS.md` hoje declara explicitamente que
  nenhum identificador do Telegram é armazenado e que não existem serviços de
  CRUD de usuário. Sem resolver qual `User` corresponde a um chat do
  Telegram, a TASK-035 não tem como gravar o proprietário da missão.
- **Classificação:** Nova TASK do MVP
- **Justificativa:** `docs/MVP.md` exige, como critério objetivo de conclusão
  do MVP, que "um usuário autorizado consegue criar e consultar uma missão
  pelo canal Telegram" — isso pressupõe uma identidade resolvível, que ainda
  não existe. Não é autenticação real (reservada à TASK-046) nem autorização
  (TASK-047): é o vínculo mínimo necessário para o próximo passo do fluxo já
  iniciado nas TASKs 032 a 034. O usuário, ao ser consultado, optou por pausar
  a TASK-035 e criar esta tarefa prévia em vez de ampliar o escopo da 035 ou
  implementar apenas a camada de apresentação sem persistência.
- **Próxima ação:** criada `docs/tasks/TASK-056.md`, fora da faixa numérica
  original (mesmo padrão da TASK-042 e da TASK-055), posicionada no roadmap
  imediatamente antes da TASK-035, que permanece bloqueada até a TASK-056 ser
  executada. `docs/ROADMAP.md`, `docs/tasks/README.md`, `AGENTS.md` e
  `docs/USERS.md` atualizados para refletir a pendência.

### DEC-010 — Nunca converter falha de interpretação em falha de transporte no webhook

- **Data:** 2026-08-07
- **Ideia:** depois que o webhook do Telegram autentica e aceita uma
  atualização, uma falha subsequente ao traduzi-la em `Intent` (cota de IA
  excedida, indisponibilidade do provedor, conteúdo inválido) não deve virar
  `500`. A entrega da atualização pelo Telegram e o processamento por IA são
  tratados como falhas independentes.
- **Classificação:** Implementar agora
- **Justificativa:** decisão do usuário durante a aprovação do plano da
  TASK-034: um `500` faria o Telegram reentregar a mesma atualização,
  potencialmente repetindo a chamada de IA sem necessidade. A falha já é
  registrada pela telemetria sanitizada da TASK-031; a rota apenas confirma o
  recebimento com `204`, sem executar ação de domínio, sem responder ao
  usuário e sem criar mecanismo de retry, fila ou execução de comando — isso
  permanece reservado às TASK-035 e TASK-036.
- **Próxima ação:** nenhuma; documentado em `docs/TELEGRAM_ADAPTER.md` e
  `docs/tasks/TASK-034.md`.

### DEC-009 — Restringir a TASK-033 à fronteira de entrada do Telegram

- **Data:** 2026-08-07
- **Ideia:** `docs/tasks/TASK-033.md` só continha o texto-modelo genérico
  ("Definir adaptação Telegram"), sem escopo detalhado. Definir o que essa
  tarefa cobre exclusivamente a partir do que já está documentado:
  representar a mensagem bruta do Telegram como contrato imutável e
  traduzi-la em um `Intent`, chamando somente o `IntentInterpreter` já
  existente (TASK-032).
- **Classificação:** Implementar agora
- **Justificativa:** `docs/ARCHITECTURE.md` lista Telegram como módulo-alvo
  com fronteira própria; `docs/TELEGRAM.md` já definia que "o adaptador deve
  traduzir mensagens em comandos ou intenções sem conter lógica de
  domínio" e que autenticação, webhooks, comandos e notificações ficam para
  tarefas posteriores. `docs/ROADMAP.md` já reserva a TASK-034 para o
  webhook real, a TASK-035 para comandos de missão, a TASK-036 para
  notificações e a TASK-037 para preferências de usuário. Incluir qualquer
  uma dessas responsabilidades na TASK-033 seria antecipar tarefas futuras,
  proibido por `AGENTS.md`.
- **Próxima ação:** nenhuma; documentado em `docs/TELEGRAM_ADAPTER.md` e
  `docs/tasks/TASK-033.md`. Webhook, comandos, notificações e preferências
  pertencem às TASKs 034 a 037.

### DEC-008 — Fechar o vocabulário de intenção da TASK-032 na documentação existente

- **Data:** 2026-08-07
- **Ideia:** definir o conjunto de `IntentKind` e parâmetros da interpretação
  de intenção estritamente a partir do que já estava documentado, sem
  adicionar nem omitir nada.
- **Classificação:** Implementar agora
- **Justificativa:** `docs/MISSION_SYSTEM.md` já define os seis comandos
  fechados de `MissionCommand` (`activate`, `pause`, `resume`, `complete`,
  `cancel`, `expire`); `docs/MVP.md` exige explicitamente que o usuário
  consiga "criar e consultar uma missão pelo canal Telegram"; e
  `docs/TELEGRAM.md` fixa as quatro fontes selecionáveis da V1. `IntentKind`
  reaproveita `MissionCommand` diretamente em vez de duplicar suas strings, e
  `IntentParameters` reaproveita os campos já existentes de
  `MissionCriteria` e `mission_sources`. Nenhum campo, comando ou fonte novos
  de domínio foram introduzidos.
- **Próxima ação:** nenhuma; documentado em `docs/INTENT_INTERPRETATION.md` e
  `docs/tasks/TASK-032.md`. Decisões de execução de comando e de canal
  pertencem às TASKs 033 em diante.

### DEC-007 — Limitar a V1 ao Gemini por nível de acesso

- **Data:** 2026-08-02
- **Ideia:** usar Gemini gratuito para USER e permitir que ADMIN/DEV tentem o melhor modelo Gemini, com retorno automático ao gratuito quando o nível pago não estiver disponível.
- **Classificação:** Implementar agora
- **Justificativa:** Gemini 3.6 Flash possui nível gratuito e já foi validado, enquanto OpenAI e Anthropic não oferecem API geral gratuita nas contas configuradas. A política mantém uma integração real na V1, evita dependência de créditos e preserva um único comportamento compartilhado para ADMIN/DEV.
- **Próxima ação:** implementar na TASK-030 USER somente com `gemini-3.6-flash` e ADMIN/DEV tentando `gemini-3.1-pro-preview` antes do fallback gratuito; registrar usuário pago e OpenAI/Claude como evolução da V2.

### DEC-006 — Corrigir a TASK-055 para fontes selecionadas pelo usuário

- **Data:** 2026-08-02
- **Ideia:** substituir o escopo exclusivo da Kabum por coleta em todas as lojas e marketplaces explicitamente selecionados pelo usuário para a V1.
- **Classificação:** Implementar agora
- **Justificativa:** a TASK-055 registrada anteriormente não representa o requisito informado pelo usuário. A correção amplia arquitetura, testes e manutenção, pois cada fonte exige um Store Provider próprio, mas continua limitada à seleção explícita e não autoriza descoberta ou integração automática de qualquer marketplace.
- **Próxima ação:** implementar na TASK-055 providers para Pichau, Terabyte, Amazon e Kabum; na TASK-035, exibir essas opções como selecionáveis e apresentar abaixo, sob ***Futuro***, Mercado Livre, Shopee e AliExpress desabilitados.

### DEC-005 — Ordenar observações de preço após coletas persistidas

- **Data:** 2026-08-02
- **Ideia:** corrigir a ordem de execução da TASK-015 para respeitar sua FK obrigatória para `collection_runs`.
- **Classificação:** Implementar agora
- **Justificativa:** executar a TASK-015 antes da TASK-026 exigiria antecipar persistência de coletas ou violar o contrato relacional e a rastreabilidade histórica definidos na TASK-010.
- **Próxima ação:** executar a TASK-016 antes do bloco de missões, seguir da TASK-019 à TASK-026, executar então a TASK-015 e, na sequência, a TASK-017.

### DEC-001 — Instituir governança de novas funcionalidades

- **Data:** 2026-08-01
- **Ideia:** registrar decisões e classificar previamente toda nova funcionalidade sugerida.
- **Classificação:** Implementar agora
- **Justificativa:** a política protege o escopo definido em `docs/MVP.md`, evita aumento de complexidade não planejado e cria rastreabilidade para decisões futuras.
- **Próxima ação:** aplicar a política no `AGENTS.md`; registrar ideias futuras em `docs/BACKLOG.md`, `docs/OUT_OF_SCOPE.md` ou no roadmap conforme sua classificação.

### DEC-002 — Instituir workflow permanente de execução de TASKs

- **Data:** 2026-08-01
- **Ideia:** padronizar preparação, validação, implementação, testes, revisão, documentação, commit e push para toda TASK.
- **Classificação:** Implementar agora
- **Justificativa:** o workflow preserva o escopo do MVP, aumenta a rastreabilidade das entregas e garante que código, documentação e repositório permaneçam sincronizados.
- **Próxima ação:** aplicar automaticamente o workflow definido em `AGENTS.md` a toda TASK futura; solicitar autorização explícita antes de cada push.

### DEC-003 — Inventariar dependências para novas máquinas

- **Data:** 2026-08-01
- **Ideia:** registrar dependências e verificar a compatibilidade do ambiente antes de iniciar TASKs em outra máquina.
- **Classificação:** Implementar agora
- **Justificativa:** evita instalações desnecessárias, mantém o ambiente reproduzível e preserva a autorização do usuário para qualquer download ou instalação.
- **Próxima ação:** manter `docs/DEPENDENCIES.md` e `backend/requirements.txt` atualizados; comparar o ambiente antes de cada nova TASK.

### DEC-004 — Acompanhar a versão estável mais recente do Python

- **Data:** 2026-08-01
- **Ideia:** manter o projeto na versão estável mais recente do Python, em vez de fixá-lo permanentemente em uma série menor antiga.
- **Classificação:** Implementar agora
- **Justificativa:** a alteração afeta somente a política de ambiente, não amplia o escopo funcional do MVP e evita instalar uma versão antiga quando a versão estável atual é compatível. Cada atualização continua condicionada à validação das dependências e dos testes aplicáveis.
- **Próxima ação:** registrar em `docs/DEPENDENCIES.md` a versão mais recente efetivamente validada e repetir a validação quando uma nova versão estável for adotada.
