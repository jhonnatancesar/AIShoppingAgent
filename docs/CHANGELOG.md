# Changelog

## 2026-08-08 — TASK-059: Groq como fallback do ADMIN/DEV + perfil configurável de validação

- Registrada `DEC-016` e criada `docs/tasks/TASK-059.md` depois que a chave
  `AISHOPPING_GROQ_API_KEY` (já presente em `backend/.env`) foi testada
  isoladamente com sucesso, fora do `AIProviderManager`.
- Criado `GroqProvider` (`backend/app/ai_provider/groq.py`) via `httpx`
  contra a API compatível com OpenAI do Groq, seguindo o mesmo contrato
  sanitizado (`AIProviderQuotaExceeded` com `quota_reset_at` do cabeçalho
  `retry-after`, `AIProviderUnavailable`, erros de autenticação/rejeição)
  já usado pelo `GeminiProvider`. `httpx` declarado explicitamente em
  `backend/requirements.txt` (já presente de forma transitiva).
- `AdminDevAIProviderManager` (`backend/app/ai_provider/manager.py`) agora
  tenta até três níveis — Gemini premium → Groq → Gemini gratuito — com o
  Groq opcional: sem `AISHOPPING_GROQ_API_KEY` configurada,
  `build_admin_dev_ai_provider_manager` mantém o comportamento de dois
  níveis já validado nas TASKs 029–031. `USER` continua sem fallback,
  inalterado.
- `IntentInterpreter.interpret` (`backend/app/intent/interpreter.py`) ganhou
  um parâmetro nomeado opcional `profile` (default `USER`, inalterado para
  o webhook de produção — TASKs 033–035), para que ferramentas de validação
  manual rodem via `ADMIN`/`DEV` sem consumir a cota gratuita compartilhada
  do `USER`.
- `backend/scripts/validate_intent_interpreter.py` ganhou a opção
  `--profile` (`admin` como padrão, `dev`, `user`), com pacing mais curto
  para `ADMIN/DEV` (5s) e mantendo o pacing conservador para `user` (60s).
- Testes novos (`tests/test_groq_provider.py`) e ajustados
  (`tests/test_gemini_user_profile.py`, `tests/test_intent_interpreter.py`).
  `scripts\check.cmd` completo aprovado: 316 testes, 94,96% de cobertura.
- **Validação real**: chamada direta ao Groq real via `AIProviderManager`
  (resposta coerente do `llama-3.3-70b-versatile`); cascata de 3 níveis
  validada de ponta a ponta com um premium real forçado a falhar por cota,
  confirmando que o Groq real é alcançado e responde. As 19 mensagens
  diversas da TASK-057 foram classificadas corretamente via `--profile
  admin`, sem tocar na cota do `USER`.
- **TASK-057 avançou**: com o perfil `ADMIN/DEV` validado, a confirmação
  final contra o `USER`/Gemini real passou de 3/19 para cobrir 3 dos 4
  `IntentKind` (`create_mission`, `query_mission`, `mission_command`); só
  `unknown` segue pendente por nova exaustão da cota gratuita do `USER`
  (`docs/tasks/TASK-057.md`).

## 2026-08-08 — TASK-057 (parcial) e TASK-058 (registro)

- Registrada `DEC-015` e criada `docs/tasks/TASK-058.md`: confirmar a
  intenção interpretada e pedir aprovação explícita do usuário antes de
  executar qualquer comando de missão. Pedido do usuário durante a execução
  da TASK-057, fora do escopo dela (altera o despacho do webhook das TASKs
  033–035); registrada como nova TASK do MVP, não implementada.
- Refinado o prompt de sistema de `backend/app/intent/interpreter.py`
  (TASK-032/057): orientação explícita de robustez a erros de digitação,
  gírias regionais, falta de acentuação/pontuação e ordem livre das
  informações na frase, mais exemplos few-shot cobrindo os quatro
  `IntentKind`, sem alterar o vocabulário fechado do contrato.
- Ampliado `backend/scripts/validate_intent_interpreter.py` para rodar um
  conjunto padrão de 19 mensagens diversas cobrindo os quatro `IntentKind`,
  com pacing entre chamadas e retry com backoff respeitando
  `quota_reset_at` quando informado pelo provedor.
- Atualizado `tests/test_intent_interpreter.py` com verificação de que o
  prompt de sistema contém a orientação de robustez. `scripts\check.cmd`
  completo aprovado em Python 3.14.6: 299 testes, 94,73% de cobertura.
- **Impedimento registrado** (`docs/tasks/TASK-057.md`): a validação manual
  real contra o Gemini ficou incompleta — apenas 3 das 19 mensagens
  (`create_mission`) foram validadas antes de a cota gratuita do perfil
  `USER` se esgotar, sem `quota_reset_at` informado. Rotear a validação pelo
  Gemini premium ou por outro provedor (ex.: a chave Anthropic presente em
  `backend/.env`) foi descartado por violar o guardrail de acesso exclusivo
  via `AIProviderManager` no perfil `USER`. TASK-057 permanece em execução
  até a validação real cobrir `query_mission`, `mission_command` e
  `unknown`.

## 2026-08-08 — TASK-035

- Semeada a migração `20260807_0002` com as quatro lojas selecionáveis da
  V1 (`pichau`, `terabyte`, `amazon`, `kabum`), necessárias para
  `MissionSource.store_id`.
- Criada `backend/app/database/dependency.py` (`get_session`), a primeira
  dependência FastAPI de sessão de banco por requisição do projeto: comita
  no sucesso, desfaz em qualquer exceção, sempre fecha a sessão.
- Criadas `list_missions_for_user`, `find_missions_by_reference` e
  `resolve_mission_for_command` (`backend/app/missions/query.py`) para
  resolver uma missão a partir de texto livre, já que a V1 não expõe
  identificador de missão ao usuário.
- Criado `create_mission_from_criteria` (`backend/app/missions/service.py`):
  cria `Mission` + `MissionCriteria` + `MissionSource` e ativa
  imediatamente. Quando o `Intent` não especifica fonte nenhuma, usa
  automaticamente as quatro fontes da V1 — toda `CREATE_MISSION` válida sai
  `active`, nunca `draft`.
- Criado `backend/app/telegram/bot_api.py` (`call_bot_api`, `send_message`),
  reaproveitado pelo script de registro do webhook para eliminar duplicação.
- `POST /telegram/webhook` agora resolve a identidade do usuário (TASK-056),
  despacha por `IntentKind` (criar, consultar, comandar, desconhecido) e
  responde ao Telegram. Definido o limite explícito entre erro conhecido de
  domínio/validação (`204` com explicação) e falha inesperada, incluindo
  `IntegrityError` residual não tratada no serviço apropriado (`500`, nunca
  mascarada) — `DEC-012`, `DEC-013`.
- Aprovado `scripts\check.cmd` completo em Python 3.14.6: 298 testes, 94,73%
  de cobertura.
- Validado de ponta a ponta contra PostgreSQL 18, o Gemini e o Telegram
  reais: criação com fonte explícita e sem fonte nenhuma (fonte-padrão
  confirmada), consulta, comando (pausar) e mensagem não reconhecida — todos
  com resposta real recebida no Telegram e o estado correspondente
  confirmado em `missions`, `mission_criteria`, `mission_sources` e
  `mission_transitions`.
- Ajustada a resposta de intenção não reconhecida para lista com marcadores
  em vez de um parágrafo único, a partir de feedback real de legibilidade
  durante a validação manual.

## 2026-08-07 — TASK-056

- Descoberto, ao preparar a TASK-035, que persistir uma missão via Telegram
  exigia `Mission.user_id` sem existir nenhuma forma de resolver qual `User`
  corresponde a uma pessoa no Telegram; TASK-035 pausada e TASK-056 criada
  como pré-requisito (`DEC-011`).
- Adicionado `User.telegram_user_id` (opcional, único, `BigInteger`) —
  exclusivamente o `user.id` do Telegram (a pessoa), nunca o `chat.id` (a
  conversa); `chat_id` não é persistido nesta tarefa.
- Adicionada a migração reversível `20260807_0001`.
- Criado `get_or_create_telegram_user` em `backend/app/users/service.py`:
  resolução determinística e idempotente, com `SAVEPOINT` protegendo contra
  a corrida de duas mensagens simultâneas do mesmo usuário novo. Sessão
  controlada pelo chamador (`session.flush()`, nunca `commit()`), mesmo
  padrão de `transition_mission`. Não conectado ao webhook da TASK-034 —
  essa decisão de sessão por requisição pertence à TASK-035.
- Aprovado `scripts\check.cmd` completo em Python 3.14.6: 272 testes, 94,50%
  de cobertura, `users/service.py` e `users/models.py` a 100%.
- Validado em PostgreSQL 18 real via Docker Compose: `get_or_create_telegram_user`
  chamado duas vezes devolveu o mesmo `User.id`; uma inserção direta
  duplicada, contornando o serviço, foi rejeitada pela constraint `UNIQUE`
  do banco; confirmada a cadeia `upgrade` → `downgrade -1` → `upgrade`
  novamente até `20260807_0001`.
- TASK-035 segue pausada e só será retomada por solicitação explícita; seu
  bloqueio de pré-requisito foi removido.

## 2026-08-07 — TASK-034

- Criada a rota `POST /telegram/webhook` (fora de `/api/v1`), que autentica
  cada atualização real do Telegram por segredo compartilhado
  (`X-Telegram-Bot-Api-Secret-Token` contra
  `AISHOPPING_TELEGRAM_WEBHOOK_SECRET`, comparação de tempo constante) e usa o
  envelope de erro padrão no `401`.
- Atualizações sem mensagem de texto (foto, callback, mensagem editada) são
  reconhecidas e ignoradas com `204`.
- Definido que, depois de uma entrega autenticada, falha de interpretação
  (cota, indisponibilidade, conteúdo inválido) nunca vira `500`: é registrada
  e a rota ainda responde `204`, para não transformar falha de IA em falha de
  transporte e evitar reentrega pelo Telegram. Nenhum mecanismo de retry,
  fila, resposta ao usuário ou execução de comando foi criado.
- Adicionado `backend/scripts/register_telegram_webhook.py` (`set`/`delete`/
  `info`), sem SDK, usando `urllib` da biblioteca padrão contra a Bot API real.
- Adicionados `AISHOPPING_TELEGRAM_BOT_TOKEN` e
  `AISHOPPING_TELEGRAM_WEBHOOK_SECRET` a `Settings`; instalado `cloudflared`
  como ferramenta de desenvolvimento para expor o webhook local durante a
  validação real.
- Aprovado `scripts\check.cmd` completo em Python 3.14.6: 265 testes, 94,41%
  de cobertura.
- Validado de ponta a ponta contra o Telegram e o Gemini reais, usando um
  túnel `cloudflared`: mensagem real recebida, autenticada, traduzida e
  processada com sucesso (`ai_provider_attempt` com `outcome=succeeded`),
  resposta `204`; requisições sem segredo válido rejeitadas com `401` sem
  chamar a IA. Webhook removido e túnel encerrado ao final.

## 2026-08-07 — Correção: integrar a branch órfã da TASK-031

- Descoberto, ao auditar o código antes de iniciar a TASK-034, que o código
  real da TASK-031 (`backend/app/ai_provider/telemetry.py` e sua fiação em
  `contracts.py`, `gemini.py` e `manager.py`) nunca tinha sido mesclado ao
  `main`: existia só na branch `task-031-ai-telemetry` (commit `b8a6f3d`),
  enviada ao GitHub mas nunca integrada, enquanto o `main` avançou por uma
  linha irmã sem esse commit.
- A documentação (`CHANGELOG.md`, `PROJECT_CONTEXT.md`, `ROADMAP.md`,
  `AGENTS.md`, `docs/tasks/TASK-031.md`) já descrevia a TASK-031 como
  concluída antes do código estar de fato no `main`, incluindo uma correção
  anterior desta mesma sessão que confiou nessa documentação sem verificar o
  código-fonte.
- Corrigido com `git cherry-pick` do commit `b8a6f3d` sobre o `main` atual,
  resolvido a favor da documentação já sincronizada com as TASKs 032 e 033
  nos cinco arquivos de texto que conflitaram; o código de telemetria foi
  aplicado sem conflito.
- Revalidado `scripts\check.cmd` completo em Python 3.14.6: 256 testes
  aprovados (incluindo `tests/test_ai_telemetry.py`, ausente até então),
  94,33% de cobertura, `telemetry.py` com 100%.

## 2026-08-07 — TASK-033

- Criado `app.telegram`, a fronteira de entrada do canal Telegram, sem
  lógica de domínio e sem SDK ou webhook do Telegram.
- Definido o contrato imutável `TelegramMessage` (`chat_id`, `user_id`,
  `text`, `received_at`), sem resolução para o `User` interno.
- Criado `TelegramIntentAdapter`, que encaminha `text` e `received_at` ao
  `IntentInterpreter` já existente (TASK-032) e devolve o `Intent`
  resultante sem inspecionar `kind` ou `command`.
- Adicionados testes unitários cobrindo o contrato e a adaptação para os
  quatro valores de `IntentKind`, aprovados junto com `scripts\check.cmd`
  completo em Python 3.14.6 (248 testes, 94,89% de cobertura); `app.telegram`
  ficou com 100% de cobertura.
- Sem integração externa nova nesta tarefa: nenhuma chamada de rede própria
  foi adicionada, então nenhum script de validação manual foi necessário.
- Documentado em `docs/TELEGRAM_ADAPTER.md`, com referência cruzada em
  `docs/TELEGRAM.md`, e registrada a decisão de escopo em
  `docs/DECISION_LOG.md` (DEC-009).

## 2026-08-07 — Validação real da TASK-032

- Executado `scripts\check.cmd` completo em Python 3.14.6 com Docker Desktop
  disponível: `pip check`, `ruff check`, `ruff format --check`, 236 testes
  aprovados com 94,79% de cobertura, grafo de migrações Alembic com uma única
  head e configuração do Docker Compose válida.
- Executada a validação manual real contra o Gemini do perfil `USER` com
  `backend/scripts/validate_intent_interpreter.py`, cobrindo os quatro
  valores de `IntentKind`: `create_mission` ("Quero um notebook gamer até R$
  5000 na Pichau ou Kabum"), `mission_command` ("pausa minha missão do
  notebook" → comando `pause`), `query_mission` ("Como está minha missão do
  notebook?") e `unknown` ("Qual é a previsão do tempo em São Paulo
  amanhã?").
- Confirmado por auditoria que o vocabulário de `IntentKind` e
  `IntentParameters` reaproveita exatamente `MissionCommand`
  (`docs/MISSION_SYSTEM.md`), os campos de `MissionCriteria`
  (`docs/MISSION_CRITERIA.md`) e as quatro fontes selecionáveis
  (`docs/TELEGRAM.md`), sem nenhum campo novo de domínio.
- Confirmado que a extração da tupla de exceções de parsing para a constante
  de módulo `_PARSING_ERRORS` não é uma correção de um bug do Ruff: com
  `target-version = "py314"`, o Ruff aplica a PEP 758 (Python 3.14) e remove
  os parênteses de `except (A, B):`, produzindo `except A, B:`, sintaxe
  válida somente a partir do Python 3.14. O ambiente de implementação
  original só tinha Python 3.10, que rejeita essa sintaxe; no Python 3.14.6
  real, ambas as formas compilam e passam por lint e testes. A constante
  nomeada foi mantida por clareza, sem necessidade de reversão.
- Nenhuma falha real foi encontrada; nenhuma correção de código foi
  necessária.

## 2026-08-07 — TASK-032

- Criado `IntentInterpreter`, agnóstico de canal, que traduz mensagens livres
  em `Intent` estruturado usando exclusivamente `AIProviderManager` no perfil
  `USER`.
- Definido vocabulário fechado de intenção (`create_mission`, `query_mission`,
  `mission_command`, `unknown`), reaproveitando diretamente `MissionCommand`
  para os seis comandos já existentes do ciclo de vida da missão e os campos
  já existentes de `MissionCriteria` para os parâmetros extraídos.
- Implementado parsing estrito da resposta do provedor, com fallback seguro
  para `unknown` em qualquer JSON inválido, campo desconhecido, valor fora do
  vocabulário fechado ou combinação inconsistente entre `kind` e `command`;
  falhas do provedor (cota, indisponibilidade) continuam propagadas, sem
  virar `unknown`.
- Adicionado script manual `backend/scripts/validate_intent_interpreter.py`
  para validação real contra o Gemini, seguindo o padrão das TASKs 029 a 031.
- Aprovados lint e formatação (`ruff check`, `ruff format --check`) e os
  novos testes unitários por leitura e revisão de código. A execução real do
  `pytest` e do script de validação com Gemini não foi possível neste
  ambiente: o sandbox de execução só tem Python 3.10 disponível (o projeto
  exige 3.14) e o download de uma versão compatível do Python via `uv` foi
  bloqueado pela política de rede do ambiente; adicionalmente, um diretório
  `.pytest_cache` órfão e sem permissão de leitura impede qualquer execução
  do `pytest` neste sandbox, independentemente da versão do Python. A
  validação real completa fica pendente da máquina do usuário, com Python
  3.14.6 já validado conforme `docs/DEPENDENCIES.md`.

## 2026-08-02 — TASK-031

- Adicionada telemetria JSON sanitizada por tentativa de IA, com correlação,
  perfil, finalidade, provedor, modelo, resultado e indicação de fallback.
- Preservado de erros `429` somente o reset recomendado por
  `google.rpc.RetryInfo`; detalhes brutos, prompts, respostas, tokens e chaves
  continuam fora dos contratos e dos eventos.
- Criado aviso seguro e agnóstico de canal para cota esgotada, informando o
  horário UTC quando conhecido ou declarando o prazo desconhecido.
- Validado o fluxo real ADMIN/DEV: o premium retornou `429` com reset informado,
  a telemetria registrou a falha e o fallback gratuito respondeu com sucesso.

## 2026-08-02 — TASK-030

- Implementada política única para ADMIN/DEV, sem perfis de IA duplicados.
- Definida tentativa em `gemini-3.1-pro-preview` com fallback para o gratuito
  `gemini-3.6-flash` em quota ou indisponibilidade.
- Mantido USER exclusivamente no nível gratuito e movidos usuário pago,
  OpenAI e Claude para a V2 pela ausência de API geral gratuita.
- Validado o fallback real com chave Free Tier: recusa `429` no premium e
  resposta não vazia com conteúdo esperado no Flash gratuito.

## 2026-08-02 — Preflight obrigatório de TASKs

- Tornada obrigatória, antes de qualquer implementação, a identificação de
  credenciais, contas, permissões, serviços, infraestrutura e ferramentas
  necessárias ao desenvolvimento e à validação real.
- Definido que recursos ausentes que dependam do usuário devem ser solicitados
  antecipadamente, com orientação de configuração segura fora do Git e do chat.
- Proibido substituir uma integração real previsível por implementação genérica
  ou validação exclusivamente mockada por falta de preparação antecipada.

## 2026-08-02 — TASK-029

- Implementado perfil USER com `google-genai` 2.16.0 e modelo padrão
  `gemini-3.6-flash`.
- Adicionadas configuração segura, tradução de mensagens e falhas sanitizadas.
- Validada chamada autenticada real pelo `AIProviderManager`, com resposta não
  vazia do modelo `gemini-3.6-flash` e conteúdo esperado.
- O endpoint real rejeitou uma chave descartável inválida e a falha foi
  sanitizada corretamente, sem exposição da credencial ou resposta bruta.

## 2026-08-02 — TASK-028

- Definidos contratos imutáveis de mensagens, requisição e resposta de IA.
- Criadas portas assíncronas para manager e providers internos e taxonomia segura
  de erros, sem integrar SDKs ou provedores reais.

## 2026-08-02 — TASK-027

- Criado avaliador determinístico de queda de preço e entrada no total-alvo para
  missões ativas.
- Adicionados candidatos tipados do catálogo, prevenção de repetição contínua e
  proteção contra disponibilidade ou moeda não comparável.

## 2026-08-02 — TASK-042

- Definido catálogo fechado e versionado com seis eventos de missão, coleta,
  preço e disponibilidade.
- Criados payloads tipados e validações executáveis sem antecipar persistência,
  publicação, consumo, alertas ou notificações.

## 2026-08-02 — TASK-017

- Criadas consultas somente leitura para listar e obter a observação mais recente
  do histórico de preços de uma oferta.
- Adicionados filtros temporais e de disponibilidade, paginação validada, total
  filtrado e ordenação determinística.

## 2026-08-02 — TASK-015

- Criadas observações append-only com preço, moeda, frete, total, fulfillment,
  disponibilidade, horários e evidência bruta.
- Adicionada revisão reversível `20260802_0012` com constraints monetárias e
  vínculos restritivos a oferta e coleta.

## 2026-08-02 — TASK-026

- Criados `CollectionRun`, estados `running`, `succeeded` e `failed` e revisão
  reversível `20260802_0011`.
- Implementados início e encerramento atômicos com bloqueio de linha.

## 2026-08-02 — TASK-025

- Implementada normalização monetária exata com `Decimal`, sem ponto flutuante.
- Separados preço do item, frete conhecido e total; frete grátis é zero e frete
  desconhecido permanece nulo.
- Validados códigos ISO 4217, símbolos monetários, consistência do frete,
  precisão e limite de `numeric(19,4)`.
- Preservados oferta bruta, vendedor, fulfillment e evidência; disponibilidade
  normalizada em três estados sem inferir estoque ausente.
- Corrigido o entrypoint Linux para LF permanente após falha real causada por
  CRLF no shebang.
- Validadas três ofertas reais de Amazon, Kabum, Pichau e Terabyte em container
  Linux; Pichau e Terabyte executaram headed via Xvfb.
- Ampliada a suíte para 134 testes aprovados e 97,68% de cobertura.

## 2026-08-02 — Complemento Linux da TASK-055

- Reaberta e concluída novamente a TASK-055 após validação Linux real.
- Adicionado entrypoint com Xvfb à imagem da API para Chromium headed sem
  interface gráfica no host.
- Incluído o validador real na imagem; Pichau e Terabyte usam headed por padrão,
  enquanto Amazon e Kabum permanecem headless.
- Localizado o Docker Desktop instalado fora do `PATH`; construída a imagem
  Linux com Python 3.14, Chromium 151 e Xvfb.
- Validadas três ofertas reais por origem: Amazon e Kabum em headless, Pichau e
  Terabyte em headed pelo display virtual, sem interface gráfica.
- Corrigida após falha real a invocação do validador para execução como módulo
  Python dentro do container.

## 2026-08-02 — TASK-055

- Implementados providers Playwright independentes para Pichau, Terabyte,
  Amazon e Kabum, preservando os campos brutos disponíveis em cada card.
- Adicionada coleta paralela de exatamente uma ou mais fontes selecionadas, com
  rejeição de duplicidade e sem providers para ML, Shopee ou AliExpress.
- Validadas Amazon, Kabum e Pichau por automação real e Terabyte por navegador
  real; documentada a proteção que pode exigir modo headed/display virtual.
- Mantida a política de não usar stealth, CAPTCHA solver ou evasão de proteção.
- Ampliada a suíte para 108 testes aprovados e 96,36% de cobertura.

## 2026-08-02 — TASK-024

- Adicionados Playwright 1.62.0 e Chromium à aplicação e à imagem Docker.
- Criada sessão assíncrona de navegador com contexto isolado, execução headless, timeouts configuráveis e downloads desabilitados.
- Implementado encerramento determinístico de página, contexto, navegador e processo Playwright, inclusive após inicialização parcial.
- Validado Chromium real com renderização e consulta de conteúdo local, sem antecipar Store Providers ou acessar marketplaces.
- Construída a imagem Docker e executado nela o mesmo smoke test com Chromium e dependências Linux reais.
- Ampliada a suíte para 101 testes aprovados e 99,80% de cobertura.
- Documentados instalação, operação, limites e sequência correta: TASK-055 antes da TASK-025.

## 2026-08-02 — Segurança do ambiente Python

- Restringido o ambiente do projeto à instalação oficial do Python da máquina, com assinatura digital válida.
- Proibidos instalação de dependências e testes em runtimes internos do Codex, plugins, caches ou ferramentas hospedeiras.
- Definido que alertas do antivírus interrompem a execução e não autorizam restauração ou exceções automáticas.
- Registrado em `docs/SECURITY_INCIDENT_LOG.md` o evento `INC-2026-08-02-001`, sua resposta, evidências sanitizadas e prevenção.
- Adicionada ao README uma observação visível sobre o ambiente Python permitido e a conduta diante de alertas.
- Corrigido o estado do `AGENTS.md`: TASK-023 concluída e TASK-024 como próxima tarefa executável.

## 2026-08-02 — TASK-023

- Criada a fronteira assíncrona entre orquestração e Store Providers, sem antecipar navegador ou providers concretos.
- Definidos contratos imutáveis para solicitação, oferta bruta e resultado de coleta, com validação de fonte e linha do tempo.
- Preservados nos resultados brutos vendedor, frete, disponibilidade e fulfillment necessários a varejistas e marketplaces.
- Implementado registro e despacho por fonte, com erros explícitos para duplicidade, ausência e violação contratual.
- Documentado o limite com as TASKs 024, 055, 025 e 026 e atualizada a referência de ambiente para Python 3.14.6.
- Validada a suíte no Python 3.14.6 com 97 testes aprovados, 99,78% de cobertura, Ruff, dependências, grafo Alembic e Docker Compose.

## 2026-08-02 — TASK-022

- Criados `MissionSchedule` e a revisão reversível `20260802_0010`, com uma agenda editável por missão.
- Implementados intervalo fixo positivo, próxima execução, última execução, ativação lógica e índice parcial de agendas habilitadas.
- Adicionada consulta determinística de missões ativas e não expiradas com `FOR UPDATE SKIP LOCKED`.
- Implementado avanço para o primeiro intervalo futuro, sem acumular backlog retroativo.
- Validada em PostgreSQL 18 a filtragem real, concorrência entre workers, constraints, downgrade e novo upgrade.
- Ampliada a suíte para 89 testes aprovados e 99,73% de cobertura.

## 2026-08-02 — Correção retroativa para marketplaces

- Corrigidas as TASKs 010, 014, 020 e 021 para suportar seleção de múltiplas fontes e vendedores terceiros da Amazon.
- Adicionados `Store.source_type`, `Seller`, `Offer.seller_id` e `MissionSource`, com integridade referencial e identidades separadas para varejo e marketplace.
- Ativação e retomada agora exigem critérios e ao menos uma fonte selecionada.
- Atualizado o contrato futuro de observações para preservar preço, frete, total, vendedor e fulfillment.
- Adicionada a revisão reversível `20260802_0009` e validada em PostgreSQL 18 com cenários válidos, constraints, downgrade e novo upgrade.
- Ampliada a suíte para 75 testes aprovados e 99,69% de cobertura.

## 2026-08-02 — Correção de escopo da TASK-055

- Corrigido o requisito que restringia a TASK-055 exclusivamente à Kabum.
- Definidas Pichau, Terabyte, Amazon e Kabum como fontes selecionáveis da V1, cada uma com Store Provider próprio e validação real.
- Mercado Livre, Shopee e AliExpress serão apresentados pelo bot sob ***Futuro***, sem seleção ou coleta na V1.
- Outras fontes e descoberta automática permanecem fora do escopo.
- Registrada a decisão `DEC-006` e sincronizados contexto, MVP, roadmap, backlog e limites de escopo.

## 2026-08-02 — TASK-021

- Criados `MissionTransition`, o vocabulário tipado `MissionCommand` e a revisão reversível `20260802_0008` com histórico append-only.
- Implementados todos os comandos e estados definidos na TASK-018, com validação de critérios, prazo e estados terminais.
- Tornadas atômicas a alteração de estado, a progressão de `state_version` e a inclusão do histórico, usando bloqueio da linha e versão esperada para concorrência.
- Validada em PostgreSQL 18 a cadeia de migration, o ciclo real completo, constraints, imutabilidade, conflito de versão, concorrência com uma única vencedora, downgrade e novo upgrade.
- Ampliada a suíte para 69 testes aprovados e 99,67% de cobertura.

## 2026-08-02 — Processo de validação

- Registrada autorização permanente para baixar e instalar dependências necessárias à execução e à validação real do projeto.
- Tornados obrigatórios testes reais e isolados de integração quando a mudança envolver banco de dados, contêiner, API ou outra infraestrutura; validação estática, mocks e testes unitários permanecem complementares.

## 2026-08-02 — TASK-020

- Criado o modelo SQLAlchemy `MissionCriteria`, limitado a um registro editável por missão.
- Adicionados busca obrigatória e preço-alvo opcional em `numeric(19,4)` pareado com moeda ISO 4217.
- Protegidos busca não vazia, valor não negativo, presença conjunta de valor e moeda e formato monetário por constraints.
- Mantidas recorrência e frequência fora dos critérios e reservadas à agenda da TASK-022, sem JSONB especulativo.
- Adicionada a revisão reversível `20260802_0007` e atualizado o registro central de modelos.
- Validada em PostgreSQL 18 a cadeia completa até a revisão `20260802_0007`, incluindo inserção válida, constraints, unicidade, chave estrangeira, downgrade, novo upgrade e sincronização da metadata pelo Alembic.
- Ampliada a suíte para 52 testes aprovados e 99,57% de cobertura.

## 2026-08-02 — TASK-019

- Criados o modelo SQLAlchemy `Mission` e o enum PostgreSQL `mission_status` com os seis estados da TASK-018.
- Adicionados proprietário obrigatório com `RESTRICT`, estado inicial `draft`, prazo opcional, versão concorrente e timestamps UTC.
- Protegidos título não vazio, prazo posterior à criação e versão não negativa por restrições no banco.
- Criados índices para listagem recente por proprietário e para expirações pendentes de estados não terminais.
- Adicionada a revisão reversível `20260802_0006` e atualizado o registro central de modelos.
- Detectadas e corrigidas durante a revisão a criação duplicada do enum e a divergência de ordenação do índice ORM.
- Validada a cadeia linear e a geração SQL offline de upgrade e downgrade; PostgreSQL real não pôde ser usado porque Docker não está disponível nesta máquina.
- Ampliada a suíte para 46 testes aprovados e 99,55% de cobertura.

## 2026-08-02 — TASK-016

- Criado o modelo SQLAlchemy `AuditEntry` para fatos auditáveis separados de logs operacionais.
- Adicionados ator opcional, ação e recurso tipados, metadata JSONB com padrão seguro e timestamp UTC.
- Protegida a referência a usuários com `RESTRICT` e criados índices históricos por recurso e ator.
- Adicionado trigger PostgreSQL que rejeita `UPDATE` e `DELETE`, tornando a tabela efetivamente append-only.
- Adicionada a revisão reversível `20260802_0005` e atualizado o registro central de modelos.
- Corrigida a ordem documental da TASK-015 para depois da TASK-026, preservando a FK obrigatória para coletas.
- Validada a cadeia linear e a geração SQL offline do Alembic; a execução em PostgreSQL não pôde ocorrer porque Docker não está disponível nesta máquina.
- Ampliada a suíte para 39 testes aprovados e 99,49% de cobertura.

## 2026-08-02 — TASK-014

- Criados os modelos SQLAlchemy `Store` e `Offer`, separando a origem nacional normalizada do anúncio estável de um produto.
- Adicionadas relações obrigatórias para produto e loja com `RESTRICT`, sem exclusão em cascata.
- Protegida a identidade por `(store_id, external_id)` quando informado e por `(store_id, url)`, com índices relacionais adicionais.
- Mantidos preço e disponibilidade fora da oferta para preservar o futuro histórico de observações.
- Adicionada a revisão reversível `20260802_0004` e atualizado o registro central de modelos.
- Detectada e corrigida durante a revisão uma expressão regex que o SQLAlchemy compilava incorretamente no DDL.
- Validada a cadeia linear e a geração SQL offline do Alembic; a execução em PostgreSQL não pôde ocorrer porque Docker não está disponível nesta máquina.
- Ampliada a suíte para 33 testes aprovados e 99,43% de cobertura.

## 2026-08-02 — TASK-013

- Criado o modelo SQLAlchemy `Product` para a identidade canônica independente de loja, oferta e preço.
- Adicionados UUID gerado pela aplicação, nome, marca e modelo opcionais, timestamps UTC e restrições contra textos em branco.
- Centralizado o relógio UTC compartilhado pelos modelos de usuário e produto.
- Adicionada a revisão reversível `20260802_0003` e atualizado o registro central de modelos.
- Definida deduplicação conservadora, sem unicidade por nome nem mesclagem automática sem evidência suficiente.
- Validada a cadeia linear e a geração SQL offline do Alembic; a execução em PostgreSQL não pôde ocorrer porque Docker não está disponível nesta máquina.
- Ampliada a suíte para 25 testes aprovados e 99,28% de cobertura.

## 2026-08-02 — TASK-012

- Criados o modelo SQLAlchemy `User` e o vocabulário tipado `UserRole` com `USER`, `ADMIN` e `DEV`.
- Adicionadas restrições para nome não vazio e papel válido, UUID gerado pela aplicação, ativação lógica e timestamps UTC.
- Criado registro central de modelos para manter a metadata do Alembic sincronizada.
- Adicionada a revisão reversível `20260802_0002` para a tabela `users`, sem credenciais, canais, API ou autorização.
- Validada em PostgreSQL 18 a inserção válida, a rejeição de `PLUS` e nome em branco, o downgrade e o novo upgrade.
- Ampliada a suíte para 20 testes aprovados e 99,17% de cobertura.

## 2026-08-02 — TASK-011

- Instalados e declarados SQLAlchemy 2.0.51, Alembic 1.18.5 e Psycopg 3.3.4 com suporte a Python 3.14 e PostgreSQL 18.
- Criadas configuração tipada da conexão, metadata declarativa única, construção de engine e fábrica de sessões sem conexão global antecipada.
- Configurado o ambiente Alembic e adicionada a baseline vazia `20260802_0001`, sem tabelas de domínio.
- Integradas as migrações à imagem da API e ao pipeline local, com comandos operacionais documentados.
- Validado em PostgreSQL 18 o ciclo upgrade, downgrade e novo upgrade em volume isolado; o volume existente do projeto foi preservado.
- Ampliada a suíte para 14 testes aprovados e 98,96% de cobertura.

## 2026-08-02 — TASK-010

- Definido o esquema relacional PostgreSQL para usuários, missões, critérios, transições, produtos, lojas, ofertas, coletas, preços, eventos e auditoria.
- Especificados tipos, chaves, relações, nulabilidade, unicidade, índices mínimos e regras monetárias e temporais.
- Incorporado o ciclo de vida da TASK-018 com estado atual, versão concorrente e histórico imutável de transições.
- Preservado o histórico de preços por observações anexadas, sem atualização destrutiva ou deduplicação de coletas repetidas.
- Delimitadas migrações, ORM e implementação persistente para as TASKs 011 a 017.

## 2026-08-02 — TASK-018

- Definidos os estados `draft`, `active`, `paused`, `completed`, `cancelled` e `expired` para missões.
- Documentados comandos, transições permitidas, condições e comportamento terminal.
- Estabelecidas invariantes para coleta, missões permanentes, concorrência, preservação de histórico e horários em UTC.
- Definido o registro mínimo e imutável de transições como entrada para a TASK-010, sem antecipar persistência ou implementação.
- Delimitadas as responsabilidades futuras das TASKs 010, 016 e 021.

## 2026-08-01 — TASK-009

- Criado pipeline local executável por `scripts\check.cmd`, compatível com a política de execução atual do Windows.
- Reunidas verificações de dependências, lint, formatação, testes, cobertura e Docker Compose com falha imediata.
- Garantida execução independente do diretório atual e restauração da senha temporária usada apenas para validar o Compose.
- Validado o pipeline completo com 9 testes aprovados e 98,65% de cobertura.

## 2026-08-01 — TASK-008

- Configurado logging JSON em `stdout` para a aplicação e os loggers do Uvicorn.
- Adicionado nível configurável por `AISHOPPING_LOG_LEVEL`.
- Criados eventos HTTP com método, caminho, status e duração, sem query string, corpo, cabeçalhos ou credenciais.
- Adicionados testes do formatador e dos fluxos HTTP de sucesso e falha.
- Validado o evento JSON real no ambiente Docker Compose.

## 2026-08-01 — TASK-007

- Definidas convenções para versionamento, rotas, métodos, códigos HTTP, JSON, erros, coleções e OpenAPI.
- Reservado `/api/v1` para endpoints de negócio e mantidos endpoints operacionais fora do prefixo.
- Alinhado `GET /health` com `operation_id`, código, descrição e resposta explícitos no OpenAPI.
- Validada a aderência do endpoint existente por testes automatizados.

## 2026-08-01 — TASK-006

- Criado módulo de saúde com `GET /health` e resposta estável `{"status":"ok"}`.
- Alterado o healthcheck do contêiner da API para usar o endpoint de vivacidade.
- Adicionados testes do contrato e do registro da rota no OpenAPI.
- Validado o endpoint por HTTP no ambiente Docker Compose, com API e PostgreSQL saudáveis.

## 2026-08-01 — TASK-005

- Configurado Pytest com descoberta explícita, validação estrita e cobertura mínima de 90%.
- Adicionados testes para os metadados FastAPI e para padrões, variáveis de ambiente e rejeição de configuração inválida.
- Separadas e documentadas as dependências de teste no conjunto de desenvolvimento.
- Validada a suíte com 4 testes aprovados e 100% de cobertura da base atual.

## 2026-08-01 — TASK-004

- Configurado Ruff para lint, ordenação de imports, modernização compatível com Python 3.14 e formatação.
- Separadas as dependências de desenvolvimento das dependências de runtime.
- Documentados comandos de verificação e correção automática.
- Corrigido o espaçamento dos blocos de importação existentes e validada toda a base atual.

## 2026-08-01 — TASK-003

- Criados Dockerfile da aplicação e Docker Compose para FastAPI e PostgreSQL 18.
- Adicionados volume persistente, healthchecks da API e do PostgreSQL e configuração local por `.env` não versionado.
- Documentados os comandos para iniciar e encerrar o ambiente local.
- Validado o ciclo completo com build sem cache, inicialização dos serviços, resposta HTTP da API, conexão do PostgreSQL e encerramento sem remoção do volume.

## 2026-08-01 — Consistência documental e versão do Python

- Confirmada pelo histórico e pelos critérios de aceite a conclusão das TASKs 000, 001 e 002.
- Sincronizados README, instruções, roadmap e índice de tarefas com o estado real do projeto.
- Adotada a política de uso da versão estável mais recente do Python, com Python 3.14.3 como versão atualmente validada.

## 2026-08-01 — Ambiente de desenvolvimento

- Criado `docs/DEPENDENCIES.md` como inventário de ferramentas e dependências da aplicação.
- Incluída no workflow a comparação de dependências em novas máquinas, com instalação somente após autorização.

## 2026-08-01 — TASK-002

- Adicionada gestão tipada de configuração por variáveis de ambiente.
- Criado exemplo seguro de configuração local e proteção para `backend/.env`.
- Validada a configuração padrão, a leitura de ambiente e a rejeição de valores inválidos.

## 2026-08-01 — TASK-001

- Criado o esqueleto mínimo da aplicação FastAPI.
- Declaradas as dependências FastAPI e Uvicorn.
- Validada a compilação, a integridade das dependências e a inicialização da aplicação.

## 2026-08-01 — Documentação

- Registrada a fase futura Marketplace Module para suporte a marketplaces.
- Mantido o escopo da V1 exclusivamente em lojas nacionais.
- Criados `docs/BACKLOG.md`, `docs/MVP.md` e `docs/OUT_OF_SCOPE.md` para controlar ideias futuras, escopo da V1 e exclusões explícitas.
- Adicionadas referências cruzadas a esses documentos no contexto do projeto e no roadmap.
- Criado `docs/DECISION_LOG.md` e adicionada política permanente de classificação prévia de novas funcionalidades no `AGENTS.md`.
- Reordenadas as dependências de ciclo de vida de missões e de catálogo de eventos; criada a TASK-055 para o Store Provider Kabum.
- Instituído o workflow oficial e permanente de execução de TASKs, com validação, testes, revisão técnica, documentação, commit convencional e push condicionado à autorização.
- Nenhuma funcionalidade foi implementada.

## 2026-08-01 — TASK-000

- Criada a estrutura inicial de diretórios.
- Criados documentos de contexto, visão, arquitetura e módulos-alvo.
- Registrados ADRs e RFCs iniciais.
- Criados arquivos individuais TASK-000 a TASK-054.
- Nenhuma funcionalidade de aplicação foi implementada.
