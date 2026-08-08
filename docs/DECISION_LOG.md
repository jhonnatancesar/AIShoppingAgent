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

### DEC-025 — Restringir a TASK-037 a preferências de notificações com skipped terminal

- **Data:** 2026-08-08
- **Ideia:** resolver a sobreposição aparente entre a TASK-037 genérica
  ("preferências de usuário") e os campos de lojas/categorias já entregues
  pela TASK-060, tornando a TASK-037 exclusivamente responsável por ativar ou
  desativar, de forma independente, notificações de queda de preço e de
  preço-alvo atingido pelo comando textual `/preferencias`.
- **Classificação:** Implementar agora.
- **Justificativa:** `docs/PROJECT_CONTEXT.md` já reservava explicitamente
  preferências de notificação à TASK-037, enquanto a TASK-060 possui escopo e
  validação próprios para cadastro. Ambas as notificações começam ativadas para
  preservar compatibilidade. Tratar opt-out como falha causaria retry infinito;
  ignorar sem histórico deixaria o consumo sem rastreabilidade. Por isso
  `skipped` é um resultado append-only, sem `failure_code`, e terminal como
  `succeeded`: não envia, não fica pendente e não reaparece ao reativar. O chat
  privado da TASK-036 continua sendo o único destino.
- **Próxima ação:** TASK-037 concluída e validada contra PostgreSQL e Telegram
  reais; TASK-060 permanece inalterada. A próxima executável é a TASK-038.

### DEC-024 — Restringir notificações Telegram a alertas e chats privados

- **Data:** 2026-08-08
- **Ideia:** implementar a TASK-036 como consumidor contínuo dos eventos
  `price.decreased.v1` e `price.target_reached.v1`, enviando somente para o
  chat privado confirmado do proprietário da missão.
- **Classificação:** Implementar agora.
- **Justificativa:** decisões anteriores reservam a TASK-036 às notificações
  proativas de alerta, enquanto preferências de notificação pertencem à TASK-037. Persistir
  chats de grupos/canais como destino automático poderia expor dados de uma
  missão a terceiros; por isso `telegram_chat_id` só é atualizado quando a Bot
  API identifica `chat.type=private` e o ID corresponde ao
  `telegram_user_id`. O consumidor usa a semântica at-least-once da TASK-044:
  rejeições da API e destinos ausentes/inativos ficam como falha rastreável e
  podem repetir; exatamente uma vez não é prometido.
- **Próxima ação:** TASK-036 concluída. A TASK-037 é a próxima executável e
  poderá definir preferências sem alterar a segurança do destino privado.

### DEC-023 — Adotar consumo at-least-once por consumidor com transação explícita

- **Data:** 2026-08-08
- **Ideia:** implementar a TASK-044 como uma fronteira genérica de consumo
  concorrente, registrando cada tentativa em histórico append-only e
  considerando concluído apenas o par evento/consumidor que possuir resultado
  `succeeded`.
- **Classificação:** Implementar agora.
- **Justificativa:** `FOR UPDATE SKIP LOCKED` distribui eventos entre
  transações concorrentes sem introduzir fila externa; manter reivindicação,
  processamento e registro sob a transação controlada pelo chamador preserva o
  lock até o desfecho. A semântica at-least-once permite retry após falha sem
  apagar evidência. Exactly-once, backoff, limite de tentativas, dead-letter
  queue, worker e integração Telegram aumentariam o escopo e pertencem a
  tarefas posteriores.
- **Próxima ação:** TASK-044 concluída e documentada em
  `docs/EVENT_CONSUMPTION.md`; a próxima tarefa executável volta a ser a
  TASK-036, que definirá o consumidor/notificação Telegram e o endereçamento
  por `chat_id` sem alterar este contrato genérico.

### DEC-022 — Reordenar TASK-036 atrás de TASK-043 e TASK-044, e restringir a TASK-043 à publicação genérica

- **Data:** 2026-08-08
- **Ideia:** ao iniciar o preflight da TASK-036 ("Criar notificações
  Telegram"), a próxima tarefa executável pela ordem do `docs/ROADMAP.md`,
  descobri que ela depende de duas coisas inexistentes: um pipeline real de
  eventos persistidos/publicados (`docs/PRICE_ALERTS.md` já atribuía
  "persistência e publicação" à TASK-043 e "consumo" à TASK-044) e um
  `chat_id` persistido para endereçar conversas do Telegram
  (`docs/USERS.md` já previa isso como responsabilidade de uma tarefa
  futura). O usuário confirmou implementar TASK-043 e TASK-044 antes de
  retomar a TASK-036. Durante a exploração para a TASK-043, ficou claro que
  nenhum dos seis tipos de evento do catálogo (TASK-042) tem hoje um
  produtor real com chamador em produção, exceto `evaluate_price_alerts`
  (TASK-027) — que também não tinha chamador, porque não existe ainda
  nenhum serviço que insira `PriceObservation` de verdade. Escopo da
  TASK-043 restrito a: tabela `events` (migração + modelo, conforme
  `docs/DATABASE.md`) e um serviço genérico `publish_event`, validado
  contra PostgreSQL real usando candidatos reais de `evaluate_price_alerts`
  — sem detectar os outros cinco tipos de evento (nenhuma TASK atribui essa
  detecção ainda) e sem nenhum worker/consumidor (TASK-044).
- **Classificação:** Implementar agora.
- **Justificativa:** a numeração da TASK não substitui dependências
  explícitas (`docs/ROADMAP.md`); implementar a TASK-036 sem um pipeline
  real de eventos e sem `chat_id` exigiria mockar exatamente o que
  `AGENTS.md` proíbe substituir por implementação incompleta. O mesmo
  padrão já foi aceito neste projeto para `AuditEntry` (TASK-016) e
  `MissionTransition` (TASK-021): tabelas append-only criadas e validadas
  contra PostgreSQL real antes de qualquer chamador de produção existir.
- **Próxima ação:** TASK-043 e TASK-044 implementadas e concluídas
  (`docs/tasks/TASK-043.md`, `docs/tasks/TASK-044.md`). A TASK-036 volta a
  ser a próxima executável, incluindo a definição do `chat_id`.

### DEC-021 — Criar a fase V1.2 com uma lista priorizada de evoluções entre a V1 e a V2

- **Data:** 2026-08-08
- **Ideia:** o usuário pediu um documento próprio para uma fase "V1.2",
  que sai depois da V1 e antes da V2, com ordem de execução definida:
  TASK-061 primeiro (já registrada), seguida por: ajustar o cadastro para
  pedir e-mail visando notificações; enviar notificações por e-mail;
  pesquisa de cupons; e um painel administrativo web com acesso/edição
  direta ao banco, login de administrador, status/consumo da aplicação e
  controles operacionais (reiniciar aplicação/banco). Pediu para pensar em
  quantos itens fazem sentido para o painel, sem criar um arquivo de TASK
  por item agora — só listar dentro do próprio documento da V1.2.
- **Classificação:** Versão futura.
- **Justificativa:** nenhum destes itens está em `docs/MVP.md` (a V1 só
  prevê notificações essenciais via Telegram, não e-mail nem cupons nem
  painel administrativo). Diferente do registro sem compromisso do
  `docs/BACKLOG.md`, o usuário quer prioridade e ordem definidas — por
  isso ganham um documento próprio (`docs/V1_2.md`) com a lista já
  ordenada, sem criar `docs/tasks/TASK-XXX.md` individuais ainda; cada
  item vira TASK de verdade (com preflight, validação e critério de
  aceite próprios) só quando for solicitado para execução.
- **Próxima ação:** criado `docs/V1_2.md` com a ordem de execução e a
  decomposição do painel administrativo; nenhuma implementação iniciada.

## Registros

### DEC-020 — Permitir armazenar e-mail em User, mantendo senha e token de fora

- **Data:** 2026-08-08
- **Ideia:** `docs/USERS.md` ("Limites") registrava "Não são armazenadas
  senhas, tokens, e-mails ou credenciais de autenticação real" como um
  invariante único. O cadastro inicial da TASK-060 pede e-mail como campo
  não sensível; senha/token continuam explicitamente fora (TASK-061,
  `DEC-019`). É preciso separar e-mail (dado pessoal comum) desse
  invariante, que na origem tratava tudo como "credencial de autenticação".
- **Classificação:** Implementar agora.
- **Justificativa:** e-mail não é, por si só, uma credencial de
  autenticação — é dado pessoal padrão em cadastros, e o usuário confirmou
  explicitamente querer incluí-lo na TASK-060 mesmo sabendo do invariante
  anterior. Senha e token continuam de fora, sem mudança nenhuma nessa
  parte. `docs/USERS.md` será atualizado para refletir a separação.
- **Próxima ação:** atualizar `docs/USERS.md` e `docs/DATABASE.md`
  removendo "e-mails" da lista de dados não armazenados, mantendo
  senha/token/credenciais de autenticação real de fora.

### DEC-019 — Criar a TASK-061 para autenticação real por usuário e senha

- **Data:** 2026-08-08
- **Ideia:** ao detalhar os campos do cadastro inicial da TASK-060, o
  usuário pediu também uma senha para autenticar no bot (usuário + senha),
  "pra saber que é ele mesmo".
- **Classificação:** Nova TASK do MVP.
- **Justificativa:** autenticação real por senha não é "dado não sensível"
  — exige hashing seguro (nunca texto puro), fluxo de verificação e
  provavelmente recuperação de conta; é uma peça de segurança com desenho
  próprio, não um campo a mais num cadastro. `docs/PROJECT_CONTEXT.md`
  ("O que não existe") já registra que não há autenticação real hoje, de
  propósito — a identidade via Telegram (`User.telegram_user_id`, TASK-056)
  já é confiável para o canal atual. O usuário concordou em tirar isso da
  TASK-060 e tratar como TASK própria quando pedir.
- **Próxima ação:** criada `docs/tasks/TASK-061.md` (stub, escopo a definir
  na validação); aguarda solicitação explícita.

### DEC-018 — Criar a TASK-060 para seleção de perfil de IA por papel, cadastro inicial e placeholder de upgrade

- **Data:** 2026-08-08
- **Ideia:** três pedidos relacionados do usuário, feitos juntos ao pedir
  para executar a TASK-058: (1) o webhook do Telegram passar a escolher o
  perfil de IA (`USER` vs `ADMIN`/`DEV`) a partir de `User.role`, em vez de
  sempre usar `USER` fixo, e o usuário (dono do projeto) ter seu próprio
  `User` elevado para `ADMIN` manualmente; (2) um fluxo de cadastro inicial
  via Telegram, capturando dados não sensíveis a definir; (3) um comando ou
  opção de "mudar de usuário/perfil" visível ao usuário, mas inativo —
  reservado para uma futura oferta de upgrade, gratuita por enquanto.
- **Classificação:** Nova TASK do MVP.
- **Justificativa:** mudar de qual perfil de IA uma interação real usa é
  uma alteração de comportamento de produção no despacho do webhook (TASKs
  033–035), afetando diretamente o invariante que separava `USER` (Gemini
  gratuito, sem fallback, reservado a usuários reais) de `ADMIN/DEV`
  (cascata premium/Groq, TASK-059) — precisa de análise própria de como o
  papel é determinado e protegido, não pode ser um ajuste dentro de outra
  TASK. O cadastro inicial introduz persistência nova (campos ainda a
  definir) e também exige TASK própria. O placeholder de "mudar de
  usuário/perfil" fica deliberadamente **inativo** — nenhuma lógica de
  cobrança, plano pago ou mudança real de papel por autoatendimento — para
  não violar `docs/OUT_OF_SCOPE.md` ("Plano PLUS", "Usuário pago"), que
  reserva qualquer oferta paga para a V2; é só uma afordance visível,
  reservada para decisão futura.
- **Próxima ação:** criada `docs/tasks/TASK-060.md`, registrada no
  roadmap; a elevação manual do usuário do próprio dono do projeto para
  `ADMIN` é uma ação pontual e deliberada dentro desta TASK, não uma
  capacidade geral exposta a qualquer usuário.

### DEC-017 — Encerrar a TASK-057 com validação real parcial e adiar mais variedade de linguagem para a V2

- **Data:** 2026-08-08
- **Ideia:** encerrar a TASK-057 aceitando a cobertura real atual — 3 dos 4
  valores de `IntentKind` confirmados contra o `USER`/Gemini real
  (`create_mission`, `query_mission`, `mission_command`); `unknown` só foi
  confirmado via a cascata `ADMIN/DEV` (Gemini premium/Groq), não contra o
  Gemini gratuito real do `USER`, por esgotamento repetido da cota
  gratuita. Testar ainda mais tipos/estilos de linguagem informal fica para
  a V2, em vez de continuar tentando fechar 100% da cobertura agora.
- **Classificação:** Versão futura (para a ampliação adicional de
  variedade de linguagem); o encerramento da TASK-057 em si é uma decisão
  de aceite explícita do usuário, não uma nova funcionalidade.
- **Justificativa:** o usuário autorizou explicitamente encerrar a TASK-057
  nesse estado ("dá a task 57 como encerrada... qualquer coisa na v2 a
  gente testa mais tipos de linguagens"). O prompt do `IntentInterpreter`
  foi refinado e validado com sucesso para os quatro `IntentKind` via
  provedores reais (`ADMIN/DEV`: Gemini premium e Groq; `USER`: Gemini
  gratuito para 3 dos 4 tipos), a suíte automatizada está aprovada, e a
  ferramenta de validação (`--profile admin`, TASK-059) já reduziu bastante
  o risco de regressão futura sem depender da cota escassa do `USER`. Não
  há indício de defeito conhecido no `unknown` — a lacuna é só de cobertura
  de confirmação real, não de comportamento incorreto observado.
- **Próxima ação:** `docs/tasks/TASK-057.md` marcada como concluída;
  registrar em `docs/BACKLOG.md` a ampliação futura de variedade de
  linguagem/gírias do `IntentInterpreter` para a V2.

### DEC-016 — Criar a TASK-059 para avaliar o Groq como fallback de cota do AIProviderManager

- **Data:** 2026-08-08
- **Ideia:** durante o impedimento de cota da TASK-057, o usuário pediu para
  testar a chave `AISHOPPING_GROQ_API_KEY` já presente em `backend/.env`,
  fora do `AIProviderManager` e sem tocar em nenhum módulo do app (script
  descartável em `scratchpad`, nunca importado por `backend/app`). A chave
  respondeu `200` com conteúdo coerente (`llama-3.3-70b-versatile`). O
  usuário pediu para registrar a possibilidade de usar o Groq como fallback
  para quando a cota gratuita do Gemini se esgotar.
- **Classificação:** Nova TASK do MVP
- **Justificativa:** um `GroqProvider` dentro de `app.ai_provider` seguindo
  o mesmo contrato agnóstico (`AIProvider`) já usado pelo Gemini é uma
  mudança arquitetural real no `AIProviderManager`, não um ajuste mecânico
  — por isso não pode ser implementada dentro de outra TASK, mesmo
  padrão que justificou TASK própria para TASK-056/057/058. Há tensão
  explícita com o invariante já documentado em `docs/AI_PROVIDER_MANAGER.md`
  ("OpenAI, Claude, usuário pago e comparação multi-IA ficam para a V2") e
  em `docs/PROJECT_CONTEXT.md` ("USER usa apenas Gemini gratuito... OpenAI,
  Claude e usuário pago ficam para a V2"): embora o Groq não esteja citado
  nominalmente nessas exclusões, o princípio por trás delas — a V1 usa
  somente o Gemini como provedor de IA — precisa ser revisto
  explicitamente antes de qualquer implementação, não assumido por
  conveniência de disponibilidade de uma chave. A motivação é real e
  relevante ao MVP (a cota gratuita do Gemini se mostrou frágil o bastante
  nesta própria sessão para bloquear validação real), mas a decisão de
  introduzir um segundo provedor no MVP exige análise própria de escopo,
  custo, confiabilidade e consistência de contrato entre respostas de
  provedores diferentes.
- **Próxima ação:** criada `docs/tasks/TASK-059.md`, registrada no roadmap;
  aguarda solicitação explícita para ser executada — incluindo, como parte
  da própria TASK, decidir se o invariante "só Gemini na V1" deve ser
  revisto.

### DEC-015 — Criar a TASK-058 para confirmação da intenção interpretada antes da execução

- **Data:** 2026-08-08
- **Ideia:** ao pedir a execução da TASK-057, o usuário pediu também que a IA
  devolva o texto interpretado da intenção e peça confirmação explícita de
  que é aquilo que a pessoa quer, antes de criar, consultar ou comandar
  qualquer missão.
- **Classificação:** Nova TASK do MVP
- **Justificativa:** é uma mudança de comportamento no fluxo de despacho do
  webhook (resposta síncrona ao comando do usuário), explicitamente fora do
  escopo da TASK-057 (`docs/tasks/TASK-057.md`, seção "Fora de escopo": "Não
  altera `app.telegram`... TASKs 033 a 035") e não coberta pela TASK-036
  (notificações proativas de alerta) nem pela TASK-037 (preferências de notificação).
  Confirmação de intenção antes de agir é uma decisão de domínio nova e
  não-trivial — mesmo padrão que justificou TASK própria para a identidade
  do Telegram (`DEC-011`) — não um refinamento mecânico que caiba em outra
  TASK já registrada. O usuário optou explicitamente por executar apenas a
  TASK-057 agora e registrar esta ideia para decisão futura, sem
  implementá-la agora.
- **Próxima ação:** criada `docs/tasks/TASK-058.md`, registrada no roadmap;
  aguarda solicitação explícita para ser executada.

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
  notificações e a TASK-037 para preferências de notificação. Incluir qualquer
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
