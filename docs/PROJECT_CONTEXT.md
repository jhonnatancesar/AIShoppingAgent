# Project Context

## Estado

Fase: perfis e telemetria de IA concluídos e validados contra o Gemini real nas
TASKs 029 a 031; interpretação de intenção da TASK-032 concluída e validada em
Python 3.14.6, com `scripts\check.cmd` completo aprovado e os quatro valores
de `IntentKind` confirmados contra o Gemini real do perfil `USER`. A TASK-033
definiu a fronteira de entrada do canal Telegram sobre o `IntentInterpreter`
existente. A TASK-034 integrou o webhook real do Telegram, autenticado e
validado de ponta a ponta contra o Telegram e o Gemini reais. A TASK-056
resolveu o pré-requisito de identidade que pausava a TASK-035 (`DEC-011`):
`User.telegram_user_id`, exclusivamente a pessoa do Telegram, nunca a
conversa, validado em PostgreSQL 18 real. A TASK-035 ("Criar comandos de
missão") fechou o loop: o webhook agora cria, consulta e comanda missões de
verdade a partir do `Intent`, respondendo ao Telegram, validado de ponta a
ponta contra PostgreSQL, Gemini e Telegram reais. A TASK-059 (`DEC-016`) foi concluída: `GroqProvider` real
integrado como terceiro nível opcional do `AdminDevAIProviderManager`
(Gemini premium → Groq → Gemini gratuito), e `IntentInterpreter.interpret`
ganhou um parâmetro opcional de perfil para validação manual via ADMIN/DEV
sem consumir a cota do `USER`. Isso desbloqueou a TASK-057 (robustez
do `IntentInterpreter` para escrita informal), concluída
(`DEC-017`): as 19 mensagens do conjunto ampliado foram validadas com
sucesso via ADMIN/DEV (premium/Groq/gratuito), e a confirmação final contra
o `USER`/Gemini real cobre 3 dos 4 `IntentKind` (`create_mission`,
`query_mission`, `mission_command`) — `unknown` não chegou a ser confirmado
contra o `USER` real por nova exaustão de cota, e o usuário aceitou
explicitamente encerrar a TASK nesse estado, adiando mais variedade de
linguagem para a V2 (`docs/tasks/TASK-057.md`, `docs/BACKLOG.md`).

A TASK-060 (`DEC-018`) está **concluída**: o webhook do Telegram agora
resolve o `User` antes de interpretar (não mais depois) e escolhe entre o
adaptador `USER` (Gemini gratuito) e o adaptador `ADMIN`/`DEV` (cascata da
TASK-059) a partir do `User.role` resolvido — validado de ponta a ponta
contra o Telegram real, incluindo o caso real em que o premium retornou
`429` e a cascata caiu para o Groq real com sucesso. O dono do projeto foi
elevado manualmente para `ADMIN`. Um comando `/cadastro` captura nome de
usuário, e-mail e preferências (lojas e categorias) em passos sequenciais,
persistidos em `users` (revisão `20260808_0001`), sem passar pelo
`IntentInterpreter`; validado de ponta a ponta contra o Telegram real. Um
comando `/upgrade` existe e é visível no bot, mas responde apenas "em
breve", sem nenhuma lógica real — placeholder deliberado para uma futura
oferta de upgrade (`docs/OUT_OF_SCOPE.md`). A TASK-061 (`DEC-019`) foi
registrada para autenticação real por usuário e senha, retirada do escopo
da TASK-060 por exigir desenho de segurança próprio; ainda não
implementada.

A TASK-043 (`DEC-022`) está **concluída**: o preflight da TASK-036 revelou
duas dependências reais não satisfeitas — um pipeline de eventos
persistidos/publicados e um `chat_id` do Telegram, nenhum dos dois
existente. A TASK-043 resolve a primeira: `events` (migração
`20260808_0003`) é uma tabela durável e append-only (trigger rejeitando
`UPDATE`/`DELETE`, mesmo padrão de `mission_transitions`/`audit_entries`),
e `app.events.service.publish_event` valida (`occurred_at` consciente de
fuso, tipo/agregado contra o catálogo da TASK-042) e persiste qualquer
evento do catálogo, com `recorded_at` gerado só pelo PostgreSQL. Validado
contra PostgreSQL real com candidatos reais de `evaluate_price_alerts`
(TASK-027) — o único produtor de eventos com lógica real hoje; a detecção
dos outros cinco tipos de evento do catálogo (mudança de estado de missão,
conclusão/falha de coleta, mudança de disponibilidade) segue sem nenhum
ponto de integração, porque nenhuma TASK a atribui ainda.
Uma revisão posterior reforçou que o `aggregate_id` persistido deve coincidir
com o identificador do agregado dentro do payload tipado, rejeitando divergências
antes de adicionar o evento à sessão.

A TASK-044 (`DEC-023`) está **concluída**: a revisão `20260808_0004` criou
`event_consumption_attempts`, histórico append-only de sucessos e falhas por
consumidor. `claim_unconsumed_events` usa ordem determinística e
`FOR UPDATE SKIP LOCKED`; `record_consumption_attempt` registra o desfecho sem
controlar o commit do chamador. O contrato é at-least-once, permite retry
ilimitado após falha e considera o sucesso somente para aquele consumidor.
Concorrência, retry, independência de consumidores, imutabilidade e reversão
da migração foram validados em PostgreSQL real descartável. Não há worker,
backoff, dead-letter queue, exactly-once ou integração Telegram.
Posteriormente, a TASK-037 acrescentou `skipped` como segundo resultado
terminal, preservando `failed` como o único resultado elegível para retry.

A TASK-036 (`DEC-024`) está **concluída**: a revisão `20260808_0005`
adicionou `User.telegram_chat_id`, atualizado somente por mensagens privadas da
própria pessoa. `telegram_price_alerts_v1` consome os eventos
`price.decreased.v1` e `price.target_reached.v1`, envia mensagens em português
pela Bot API e registra sucesso/falha append-only pela TASK-044. Rejeição
`ok=false` agora é falha real, não falso sucesso. O worker contínuo roda como
`app.telegram.worker` e como serviço Compose `telegram_notifier`, validado no
Docker Linux headless. PostgreSQL real confirmou a migração reversível; um
evento temporário foi entregue ao Telegram real, registrado como `succeeded` e
revertido sem resíduos.

A TASK-037 (`DEC-025`) está **concluída** e foi restringida exclusivamente a
preferências de notificações, sem alterar o cadastro da TASK-060. A revisão
`20260808_0006` adicionou `notify_price_decreases` e
`notify_target_reached`, ativados por padrão, e estendeu
`ConsumptionOutcome` com `skipped`. `/preferencias` consulta e altera cada
opção por texto, sem IA nem botões. Um evento suprimido fica terminal, sem
retry, pendência ou reenvio retroativo. PostgreSQL real confirmou defaults,
migração reversível e terminalidade; o Telegram real recebeu os comandos e
somente o evento novo depois da reativação.

A TASK-038 (`DEC-026`) está **concluída**: `app.purchase` produz uma única
recomendação determinística para missão ativa usando somente coletas
`succeeded` da própria missão e fontes selecionadas. A oferta precisa estar
disponível, usar exatamente a moeda do critério e possuir frete conhecido;
frete nulo nunca é zero/grátis e não há conversão monetária. O menor total
vence com desempate estável, enquanto evidências inelegíveis e vendedor
opcional permanecem no resultado. Falta de candidata válida retorna
`insufficient_data`. PostgreSQL real confirmou o fluxo e o rollback sem
resíduos.

A TASK-039 (`DEC-027`) está **concluída**: `app.purchase` compara todas as
evidências da TASK-038, atribui posições consecutivas somente às elegíveis e
reutiliza a mesma ordenação da recomendação. Por isso, a posição 1 é
invariavelmente a oferta recomendada para os mesmos dados. Inelegíveis ficam
depois e nunca são ordenadas por preço; frete desconhecido mantém o preço do
produto, mas seu total permanece `None` com exclusão explícita. PostgreSQL real
confirmou o ranking, o histórico, `insufficient_data` e o rollback sem resíduos.

A TASK-040 (`DEC-028`) está **concluída**: qualquer oferta elegível pode gerar
uma confirmação temporária para o proprietário da missão. A solicitação guarda
missão, oferta, observação original e proprietário, expõe o snapshot completo e
expira após 15 minutos em UTC. A TASK-041 (`DEC-029`) integrou essa confirmação
à persistência: solicitação imutável e `requested` são atômicas; resoluções são
append-only, idempotentes e protegidas contra corrida pelo PostgreSQL.
`confirm` expirado produz `stale/expired` antes de qualquer recálculo; dentro do
TTL, somente mudança material produz `stale/evidence_changed`, portanto uma
observação nova equivalente continua válida. `cancel` independe do TTL. A
validação PostgreSQL 18 cobriu recuperação, constraints, triggers, FKs e
concorrência real. Não há ação financeira; a próxima tarefa executável é a
TASK-045.

A TASK-058 (`DEC-015`) está **concluída**: `create_mission` e
`mission_command` não executam mais direto — ficam encenados em
`User.pending_intent` e só executam após confirmação explícita, descrita em
português para o usuário. A classificação de confirmar/cancelar passa por
um classificador de IA dedicado (`interpret_confirmation_reply`, prompt e
propósito próprios, fora do vocabulário fechado do `IntentInterpreter`),
reconhecendo respostas informais e erros de português, não só palavra
exata. Validado de ponta a ponta contra o Telegram real: criar missão sem
citar loja, cancelar com frase informal, e confirmar comando de missão
funcionaram corretamente. Duas correções reais surgiram dessa validação:
(1) a chave Gemini deixou de ser compartilhada entre perfis —
`AISHOPPING_GEMINI_API_KEY_USER` e `AISHOPPING_GEMINI_API_KEY_ADMIN_DEV`
são credenciais distintas; (2) `AdminDevAIProviderManager`/
`UserAIProviderManager` tinham `validate_provider_response` fora do
try/except, então uma resposta reprovada por essa checagem escapava sem
telemetria; e o adaptador do Telegram parou de repassar
`message.received_at` (relógio do Telegram) como `requested_at`, evitando
comparar relógios de fontes diferentes — a causa raiz de falhas silenciosas
reais observadas em produção.

## O que existe

- Estrutura de diretórios do projeto.
- Esqueleto mínimo da aplicação FastAPI em `backend/app/`, com dependências declaradas em `backend/requirements.txt`.
- Gestão de configuração tipada em `backend/app/core/config.py`, com variáveis de ambiente, exemplo local e arquivo `.env` ignorado pelo Git.
- Inventário de dependências e procedimento de preparação de novas máquinas em `docs/DEPENDENCIES.md`.
- Política de uso da versão estável mais recente do Python; Python 3.14.6 é a versão atualmente validada.
- Ambiente Docker Compose com contêineres FastAPI e PostgreSQL 18, volume persistente e configuração local protegida.
- Qualidade de código configurada com Ruff para lint, imports, modernização Python 3.14 e formatação.
- Testes base configurados com Pytest e cobertura mínima de 90% para o pacote da aplicação.
- Módulo de saúde com endpoint de vivacidade `GET /health`, integrado ao healthcheck do contêiner da API.
- Convenções HTTP e OpenAPI definidas em `docs/API_CONVENTIONS.md`, com endpoints de negócio versionados sob `/api/v1`.
- Logging JSON em `stdout`, com nível configurável e eventos HTTP sem captura de dados sensíveis.
- Pipeline local único para dependências, lint, formatação, testes, cobertura e validação do Docker Compose.
- Contrato de ciclo de vida de missões com estados, comandos, transições, invariantes e auditoria mínima definidos antes do modelo persistente.
- Modelo relacional PostgreSQL do MVP definido com entidades, tipos, relações, restrições, índices e regras de preservação histórica.
- SQLAlchemy, Psycopg e Alembic configurados com conexão tipada, metadata compartilhada, sessões explícitas e baseline reversível.
- Entidade `User` persistente com UUID, nome, papel tipado, ativação lógica,
  identidade da pessoa e destino privado do Telegram, campos opcionais de cadastro inicial (nome de
  usuário, e-mail, lojas favoritas, categorias preferidas, passo de
  cadastro pendente — TASK-060), timestamps e restrições de integridade.
- Entidade `Product` persistente com identidade canônica, nome, marca e modelo opcionais, timestamps e restrições de integridade.
- Entidades `Store`, `Seller` e `Offer` persistentes com fonte tipada, relações restritivas e identidade estável por varejista ou vendedor de marketplace.
- Entidade `AuditEntry` persistente e append-only, com JSONB sanitizado, referência opcional de ator e índices históricos.
- Entidade `Mission` persistente com proprietário, seis estados, prazo opcional, versão concorrente e índices operacionais.
- Entidade `MissionCriteria` persistente com busca obrigatória e preço-alvo monetário opcional, única por missão.
- Entidade `MissionTransition` append-only e serviço atômico para executar o ciclo de vida com critérios, prazo e concorrência protegidos.
- Seleção persistente de múltiplas fontes por missão, exigida na ativação e retomada.
- Fontes tipadas como varejista ou marketplace, vendedores persistentes e ofertas identificadas por vendedor.
- Agenda recorrente persistente por missão, com seleção concorrente de execuções vencidas e progressão sem backlog retroativo.
- Adaptador assíncrono de coleta com contratos de entrada e saída bruta, registro por fonte e preservação de vendedor, frete e fulfillment.
- Base Playwright 1.62.0 com Chromium isolado, ciclo de vida assíncrono, timeouts e downloads desabilitados por padrão.
- Store Providers para Pichau, Terabyte, Amazon e Kabum, com execução Linux
  headless ou headed via Xvfb conforme a origem.
- Normalização monetária exata com `Decimal`, separação de item/frete/total,
  validação de moeda e disponibilidade tipada.
- Consultas somente leitura, paginadas e determinísticas do histórico de preços,
  com filtros por período e disponibilidade e acesso à observação mais recente.
- Catálogo fechado e versionado de eventos de missão, coleta, preço e
  disponibilidade, com agregados e payloads tipados e validados.
- Avaliador de alertas para queda de preço e entrada no total-alvo, restrito a
  missões ativas, ofertas disponíveis e moedas comparáveis.
- Contratos imutáveis e agnósticos para mensagens, requisições, respostas,
  providers internos e a porta única `AIProviderManager`.
- Adaptador Gemini do perfil USER com SDK oficial, cliente assíncrono,
  configuração segura e tradução sanitizada de falhas.
- Política compartilhada de ADMIN/DEV que tenta o Gemini premium, depois o
  Groq (`GroqProvider`, TASK-059, opcional e via `httpx`) e por fim o Gemini
  gratuito; sem a chave do Groq configurada, mantém o comportamento de dois
  níveis já validado nas TASKs 029–031. Cascata validada de ponta a ponta
  contra o Groq real.
- Telemetria estruturada e sanitizada de tentativas de IA, diferenciando modelo
  premium, fallback gratuito, resultado e reset de cota quando informado.
- Aviso de cota agnóstico de canal, com prazo conhecido em UTC ou indicação
  explícita de prazo desconhecido.
- Interpretação de intenção (`IntentInterpreter`) agnóstica de canal, que
  traduz mensagens livres em `Intent` estruturado via `AIProviderManager`,
  perfil `USER` por padrão, reaproveitando `MissionCommand` e os campos
  existentes de `MissionCriteria`, com parsing estrito e fallback seguro
  para `unknown`. Prompt de sistema refinado (TASK-057) com orientação
  explícita de robustez a escrita informal, gírias, erros de digitação e
  ordem livre das informações, sem alterar o vocabulário fechado. Desde a
  TASK-059, `interpret` aceita um parâmetro opcional de perfil (`ADMIN`/`DEV`);
  desde a TASK-060, o webhook de produção o usa de verdade, escolhendo o
  perfil a partir do `User.role` resolvido em vez de ficar fixo em `USER`.
- Fronteira de entrada do canal Telegram (`TelegramMessage`,
  `TelegramIntentAdapter`) que traduz uma mensagem bruta do Telegram em um
  `Intent`, reaproveitando exclusivamente o `IntentInterpreter`.
- Webhook real `POST /telegram/webhook`, autenticado por segredo compartilhado,
  que recebe atualizações do Telegram, traduz mensagens de texto em `Intent`,
  script manual de registro contra a Bot API real.
- Resolução get-or-create de identidade (`get_or_create_telegram_user`) que
  vincula `User.telegram_user_id` — exclusivamente a pessoa do Telegram,
  nunca a conversa — de forma determinística e idempotente, protegida contra
  corrida de criação concorrente por `SAVEPOINT`, sem autenticação real.
- Despacho de comandos de missão pelo webhook: consulta responde direto;
  criar e comandar missão ficam encenados em `User.pending_intent` e só
  executam após confirmação explícita (TASK-058), com resposta síncrona ao
  Telegram (`send_message`); toda `CREATE_MISSION` válida sai `active`,
  usando as quatro fontes-padrão da V1 quando o `Intent` não especifica
  nenhuma; erro conhecido de domínio responde `204` com explicação, falha
  inesperada sobe como `500`, nunca mascarada. Primeira dependência FastAPI
  de sessão de banco por requisição (`get_session`).
- Confirmação da intenção interpretada antes de executar (TASK-058):
  classificador de IA dedicado (`interpret_confirmation_reply`,
  `backend/app/telegram/confirmation.py`), fora do vocabulário fechado do
  `IntentInterpreter`, reconhece confirmação/cancelamento em linguagem
  informal via o `AIProviderManager` do próprio perfil do usuário.
  Validado de ponta a ponta contra o Telegram real.
- Seed das quatro lojas selecionáveis da V1 (Pichau, Terabyte, Amazon,
  Kabum) em `stores`, necessário para `MissionSource`.
- Seleção do perfil de IA do webhook a partir do `User.role` resolvido
  (TASK-060): `USER` sempre usa o adaptador Gemini gratuito, `ADMIN`/`DEV`
  sempre a cascata do `AdminDevAIProviderManager`; validado de ponta a
  ponta contra o Telegram real, incluindo o caso real de fallback para o
  Groq. Comando `/cadastro` captura nome de usuário, e-mail, lojas
  favoritas e categorias preferidas em passos sequenciais persistidos em
  `users`, interceptando a mensagem seguinte do usuário sem passar pelo
  `IntentInterpreter`. Comando `/upgrade` visível no bot, inativo (só "em
  breve").
- Registro durável e append-only de eventos de domínio (`events`, migração
  `20260808_0003`) e o serviço genérico `app.events.service.publish_event`
  (TASK-043), que valida qualquer evento do catálogo fechado (TASK-042) e o
  persiste com `recorded_at` gerado só pelo PostgreSQL. Validado contra
  PostgreSQL real usando candidatos reais de `evaluate_price_alerts`
  (TASK-027).
- Consumo durável at-least-once por consumidor (`event_consumption_attempts`,
  migração `20260808_0004`), com reivindicação transacional concorrente,
  histórico append-only de sucesso/falha e retry após falha (TASK-044).
- Notificações proativas dos alertas de preço pelo consumidor
  `telegram_price_alerts_v1`, com destino privado persistido, mensagens
  sanitizadas e worker contínuo no Docker Compose (TASK-036).
- Preferências independentes de notificações de queda e preço-alvo pelo comando
  `/preferencias`; supressões ficam terminalmente `skipped` (TASK-037).
- Recomendação determinística e somente leitura por missão ativa
  (`app.purchase`, TASK-038), com menor custo total determinável na moeda do
  critério, evidências históricas identificáveis e vendedor opcional.
- Comparação completa e somente leitura das mesmas evidências (`app.purchase`,
  TASK-039), com ranking exclusivo das elegíveis e posição 1 invariável em
  relação à recomendação.
- Confirmação explícita com TTL (`app.purchase`, TASK-040) e persistência
  imutável/append-only (`purchase_confirmations` e `purchase_trail_entries`,
  TASK-041), vinculada ao proprietário e à observação original, com
  revalidação material, recuperação e idempotência concorrente.
- Documentos de visão, arquitetura, dados, módulos-alvo, escopo do MVP, backlog, itens fora de escopo, governança de decisões e workflow permanente de execução.
- ADRs, RFCs e 62 tarefas planejadas.

## O que não existe

Além de `users`, `products`, `stores`, `sellers`, `offers`, `audit_entries`,
`missions`, `mission_criteria`, `mission_sources`, `mission_transitions`,
`mission_schedules`, `collection_runs`, `price_observations`, `events`,
`event_consumption_attempts`, `purchase_confirmations` e
`purchase_trail_entries`, não
há outras tabelas de domínio implementadas. Também não existem autenticação real (senha, token, OAuth — TASK-061),
autorização por papel de fato aplicada além da seleção de perfil de IA,
teclado interativo de seleção de fontes, mudança real de plano/perfil pelo próprio usuário (o
`/upgrade` da TASK-060 é só um placeholder inativo), APIs de negócio,
worker/scheduler de coleta, detecção de `mission.status_changed`/`collection.completed`/
`collection.failed`/`offer.availability_changed` (nenhuma TASK a atribui
ainda), inserção real de `PriceObservation` num fluxo de coleta
orquestrado, suíte permanente de testes ponta a ponta nem credenciais reais
configuradas. Também não existe qualquer execução financeira; confirmação
persistente significa somente consentimento registrado.

## Invariantes

- Monólito modular.
- PostgreSQL no MVP.
- Preços são históricos, não um valor substituível.
- IA é acessada somente via AI Provider Manager.
- Funcionalidades devem seguir a tarefa explicitamente solicitada.
- A V1 pesquisa somente lojas e marketplaces explicitamente selecionados; cada fonte exige Store Provider próprio e validação real.
- `docs/MVP.md` é a definição completa do escopo da V1; `docs/OUT_OF_SCOPE.md` previne aumento de escopo e `docs/BACKLOG.md` registra evoluções futuras.
- `docs/DECISION_LOG.md` registra decisões arquiteturais e funcionais; toda nova funcionalidade deve ser analisada e classificada antes de qualquer implementação.
- A TASK-055 implementou Store Providers para Pichau, Terabyte, Amazon e Kabum. O bot permitirá escolher uma ou mais dessas fontes e mostrará Mercado Livre, Shopee e AliExpress como ***Futuro***, sem seleção ou coleta na V1.
- O workflow oficial de execução de TASKs está definido em `AGENTS.md` e deve ser seguido automaticamente em todas as conversas futuras.
- Toda TASK começa com um preflight de credenciais, contas, permissões, serviços,
  infraestrutura e ferramentas necessárias ao desenvolvimento e à validação real.
  Pendências que dependam do usuário são solicitadas antes da implementação;
  segredos ficam fora do Git e do chat.
- Antes de iniciar uma TASK em uma máquina nova, as dependências devem ser comparadas com `docs/DEPENDENCIES.md`; existe autorização permanente para instalar o necessário à execução e à validação real, respeitando as confirmações e proteções do sistema.
- O projeto acompanha a versão estável mais recente do Python e exige nova validação de compatibilidade a cada atualização.
- O ambiente usa somente o Python oficial da máquina; incidentes e respostas de segurança do ambiente são registrados em `docs/SECURITY_INCIDENT_LOG.md`.
- O ciclo de vida definido em `docs/MISSION_SYSTEM.md` orienta o modelo de dados da TASK-010 e sua execução atômica implementada na TASK-021.
- `docs/DATABASE.md` é o contrato do modelo relacional; a TASK-011 deve preparar sua evolução por migrações antes da implementação das entidades.
- Toda alteração persistente deve usar a metadata compartilhada e receber uma revisão Alembic revisada; credenciais de banco não possuem padrão inseguro.
- Usuários aceitam somente os papéis `USER`, `ADMIN` e `DEV`; `PLUS` permanece fora do MVP, e `is_active` não substitui as regras futuras de autenticação e autorização.
- Produtos são identidades canônicas independentes de loja; nomes não são únicos e nenhuma deduplicação automática ocorre sem evidência suficiente.
- Ofertas identificam anúncios estáveis por loja e nunca armazenam preço ou disponibilidade corrente; lojas persistentes não implementam providers de coleta.
- Marketplaces possuem vendedores próprios; a identidade da oferta inclui vendedor, enquanto frete e fulfillment pertencem à observação histórica.
- Auditoria é append-only; correções geram novas entradas, e metadata nunca contém segredos ou dados pessoais desnecessários.
- Missões nascem em `draft`; alterações de estado e de `state_version` são executadas atomicamente com histórico append-only e versão concorrente.
- Critérios usam busca textual e preço-alvo opcional pareado com moeda; recorrência usa agenda separada com intervalo fixo positivo.
- Eventos usam nomes versionados e payloads mínimos do catálogo; tipos
  desconhecidos e payloads incompatíveis são rejeitados antes da
  persistência ou publicação (TASK-043).
- `events` é append-only: nenhuma linha publicada é alterada ou removida,
  reforçado por trigger no banco (mesmo padrão de `mission_transitions` e
  `audit_entries`). `recorded_at` é gerado exclusivamente pelo PostgreSQL,
  nunca pela aplicação.
- Tentativas de consumo são append-only e at-least-once por consumidor: falha
  mantém o evento elegível; sucesso ou descarte por preferência o encerram só para o mesmo
  `consumer_name`; reivindicação, processamento e registro devem compartilhar
  a transação controlada pelo chamador (TASK-044).
- O destino Telegram é sempre o chat privado correspondente à pessoa; chats de
  grupo, supergrupo e canal nunca são persistidos automaticamente. Entregas de
  alerta são at-least-once e podem se repetir se a API aceitar a mensagem antes
  de um rollback do banco (TASK-036).
- Preferências de queda e preço-alvo começam ativadas; `skipped` é terminal e
  sem código de falha, portanto opt-out não gera retry nem backlog retroativo
  (TASK-037).
- Alertas são candidatos determinísticos derivados do histórico; persistência,
  publicação, consumo e notificação permanecem desacoplados.
- Recomendações usam apenas a observação corrente de coletas bem-sucedidas da
  própria missão em fontes selecionadas. Frete desconhecido e moeda diferente
  tornam a oferta inelegível; `insufficient_data` substitui qualquer escolha
  parcial, e vendedor permanece evidência opcional (TASK-038).
- Comparações reutilizam exatamente a elegibilidade, as evidências e a ordem da
  recomendação. Apenas elegíveis recebem posição; a posição 1 coincide com a
  recomendação e frete desconhecido nunca produz `total_amount` (TASK-039).
- Confirmações pertencem ao dono da missão, preservam a observação original e
  expiram em 15 minutos. Nova observação equivalente continua válida; mudança
  material ou `confirm` expirado produz `stale`, enquanto `cancel` independe do
  TTL. Solicitação e trilha são imutáveis/append-only, idempotentes e protegidas
  por índice único terminal, sem autorizar ação financeira (TASKs 040 e 041).
- Módulos da aplicação acessam IA somente por `AIProviderManager`; USER usa apenas
  Gemini gratuito, sem fallback. ADMIN/DEV tenta Gemini premium, depois o Groq
  (opcional, TASK-059, só se configurado) e por fim o Gemini gratuito. OpenAI,
  Claude e usuário pago ficam para a V2 — o Groq é fallback interno de
  infraestrutura, nunca escolha exposta ao usuário. Credenciais nunca são
  versionadas.
