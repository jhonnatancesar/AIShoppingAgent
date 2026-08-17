# Project Context

**Atualização 2026-08-16 (TASK-088):** `/listar_missoes`, o alias com hífen e
as entradas textuais `missoes`/`missões` restauram a consulta explícita sem
reintroduzir o roteador universal por IA. A resposta autenticada lista até 15
missões recentes em formato numerado, agrupadas por ativas, pausadas e
canceladas, nessa ordem, e com status visual `🟢`/`⏸️`/`❌`, sempre com
ownership no banco. `completed`, `expired` e missões de
outro usuário ficam fora. O menu nativo inclui o novo comando.

**Atualização operacional 2026-08-16 (estado autoritativo):** o Windows Server
executa o HEAD `0e90cf0805a24cfd873d4d0257dacd8ae03c7920`, já presente em
`origin/main`. Foram implantados os pacotes de IA/Firecrawl, TASK-086,
TASK-076 e TASK-087. Os 7 serviços estão saudáveis, Alembic está em
`20260811_0001`, Tailscale Funnel e webhook Telegram estão válidos. O WSL2
está limitado a 4 GB de RAM, 2 GB de swap e reclaim gradual. Após a manutenção,
4 schedules de missões `active` ficaram habilitados e todos os schedules de
missões `cancelled`, `completed` ou `expired` ficaram desabilitados. Nenhum
estado lógico de missão ou `MissionTransition` foi alterado. Este bloco
substitui referências históricas abaixo que ainda descrevam TASK-086 como não
iniciada ou TASK-076 como aguardando retomada. TASK-077 e TASK-084 permanecem
pendentes e não iniciadas.

**Atualização 2026-08-16 (TASK-087):** a revisão completa de UX/copy dos textos
visíveis está concluída. Telegram, autenticação web, cadastro, preferências,
notificações e privacidade seguem o catálogo aprovado, com listas em
`1 — Opção`, confirmações em linhas próprias e melhor leitura móvel. Comandos,
parsers, estados, TTLs e regras funcionais foram preservados. A suíte focada
aprovou 294 testes; a não-integração aprovou 1.186 testes, 1 ignorado e 90,68%
de cobertura.

**Atualização 2026-08-16 (TASK-076):** falhas dos Store Providers agora deixam
diagnóstico estruturado suficiente em `collection_source_failed` (classe,
detalhe seguro, status, etapa e traceback limitado), somente no ponto local da
orquestração. O formatter global, retry, providers e fluxos funcionais não
mudaram. A TASK-076 está concluída e validada; permanecem TASK-077 e TASK-084.

**Atualização 2026-08-16 (TASK-086):** o drift do `alembic check` está
resolvido. O Alembic 1.19.1 ignorava na metadata os checks `_type_bound`
gerados por `Enum`; as três constraints agora são explícitas nos models, sem
migration e sem mudança semântica. PostgreSQL 18.4 descartável aprovou head
`20260811_0001`, check limpo e 29 integrações. Banco e containers ativos
permaneceram intocados. A TASK-076 será retomada do stash local.

**Atualização 2026-08-15 (roteamento de IA):** `DEC-061` mantém USER e DEV
exclusivamente gratuitos: requisições normais usam Gemini → Groq
`openai/gpt-oss-120b` → OpenRouter `openrouter/free`. Grounding é opt-in e
exclusivo de DEV: Firecrawl Search API v2 direta pesquisa primeiro e somente
fontes válidas seguem como dados não confiáveis para a mesma cascata gratuita.
ADMIN compartilha a cascata histórica, sem política separada. A TASK-086
permanece não iniciada.

**Firecrawl direto (preparação local):** o cliente mínimo da Search API v2 lê
`data.web` e preserva `warning`, `id` e `creditsUsed`; uma validação real isolada
retornou HTTP 200, dois resultados web e `creditsUsed=2`. A porta agora antecede
a cascata gratuita no grounding DEV; nenhuma validação real adicional foi feita.

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
elevado manualmente para `ADMIN` nessa etapa e depois para `DEV` pela operação
one-shot controlada da TASK-047. Um comando `/cadastro` captura nome de
usuário, e-mail e preferências (lojas e categorias) em passos sequenciais,
persistidos em `users` (revisão `20260808_0001`), sem passar pelo
`IntentInterpreter`; validado de ponta a ponta contra o Telegram real. Um
comando `/upgrade` existe e é visível no bot, mas responde apenas "em
breve", sem nenhuma lógica real — placeholder deliberado para uma futura
oferta de upgrade (`docs/OUT_OF_SCOPE.md`). A autenticação por senha foi
separada na TASK-061 e depois concluída com desenho próprio (`DEC-035`).

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
controlar o commit do chamador. O contrato é at-least-once e considera o
resultado somente para aquele consumidor.
Concorrência, retry, independência de consumidores, imutabilidade e reversão
da migração foram validados em PostgreSQL real descartável. Não há worker,
backoff, dead-letter queue, exactly-once ou integração Telegram em seu escopo
original.
Posteriormente, a TASK-037 acrescentou `skipped` como segundo resultado
terminal. A TASK-049 limitou retries, acrescentou `next_retry_at` e
`dead_lettered` sem abandonar o histórico append-only.

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
concorrência real. Não há ação financeira.

A TASK-045 (`DEC-031`) está **concluída**: API e worker expõem métricas de
cardinalidade limitada para scrape direto do Prometheus; traces sanitizados
seguem por OTLP/HTTP ao Collector e Jaeger. Logs JSON correlacionam
`request_id`, `trace_id` e `span_id`; UUID externo inválido é substituído e não
tem semântica de segurança. `/health` é liveness sem banco, `/ready` consulta o
PostgreSQL com timeout e não depende da observabilidade. `/metrics` e `/health`
não geram traces; SQL exporta somente sistema e operação, sem statement,
parâmetros, DSN ou resultados. Compose headless inclui Collector, Prometheus,
Jaeger e regras que somente detectam estado, sem Alertmanager. A validação
real cobriu falha/recuperação, canários e regra em `firing`.

A TASK-046 (`DEC-032`) está **concluída**: o segredo do webhook continua
autenticando o transporte em tempo constante; somente depois a aplicação aceita
`message.from.id`, exclusivamente em chat privado direto com
`chat.id == message.from.id`. O `User` é resolvido/provisionado após essas
validações e precisa estar ativo antes de IA, domínio ou qualquer mutação.
Recusas são terminais em `204` e registram somente motivo fechado. PostgreSQL
18, API Docker e Telegram reais confirmaram primeiro contato, idempotência,
inativo, ausência de efeitos e logs sanitizados. A TASK-061 preserva essa
fronteira e acrescenta sessão por senha depois dela.

A TASK-047 (`DEC-034`) está **concluída**: `app.authorization` aplica uma
matriz fail-closed com papel único e herança `USER ⊂ ADMIN ⊂ DEV` depois da
autenticação e antes de IA/domínio. Novos usuários do Telegram continuam
sempre USER; não existe promoção pública. Ownership permanece obrigatório
inclusive para DEV, e recomendação/comparação agora exigem o proprietário na
própria consulta. Recusas encerram em `204`, sem efeito funcional, e geram
somente `authorization.denied` sanitizado. O proprietário ativo, previamente o
único ADMIN, foi promovido para DEV por UUID explicitamente verificado em uma
operação one-shot com `user.role_changed` auditado, sem migration ou lógica de
startup. PostgreSQL 18, API Docker e Telegram reais confirmaram o fluxo.

A TASK-061 (`DEC-035`) está **concluída**: Argon2id protege credenciais;
tokens descartáveis de 10 minutos ligam servidor, usuário, Telegram e ação;
sessões persistentes duram 12 horas sem renovação. `/recuperar` cria a primeira
senha ou redefine a existente; `/entrar` e `/sair` controlam a sessão. A senha
passa somente pelo formulário HTTPS, nunca pelo chat.
Troca/recuperação revogam sessões, e limites persistentes protegem login,
token e recuperação. PostgreSQL 18, concorrência, API/worker Docker, navegador,
HTTPS público e Bot API reais foram validados; canários permaneceram ausentes
da telemetria e auditoria.

A TASK-048 (`DEC-036`) está **concluída**: produção aceita secrets somente por
`*_FILE`; Compose monta `/run/secrets` com seis arquivos na API, dois no worker
e um no PostgreSQL. Valor direto/conflitante/vazio falha fechado. API e worker
executam como UID non-root; Gitleaks 8.29.1 fixado e verificado examina working
tree, versão e histórico. Docker real isolado confirmou ausência de canários em
inspect, imagem, filesystem, logs, métricas e Jaeger, e uma rotação PostgreSQL
real rejeitou a senha antiga após recriar consumidores. A próxima tarefa
executável é a TASK-049. O pipeline terminou com 573 testes e 92,53% de
cobertura.

A TASK-049 (`DEC-037`) está **concluída**: corpos HTTP são limitados a 64 KiB;
o webhook persiste recibos append-only e únicos por `update_id`, com cota de 20
updates autenticados/minuto por usuário e atomicidade entre recibo aceito e
efeitos. Operações externas têm timeout; somente leituras seguras recebem retry
com jitter, enquanto `sendMessage` ambíguo nunca é repetido cegamente.
Circuit breakers locais são independentes por Telegram, provider/modelo de IA
e Store Provider. A revisão `20260808_0009` acrescenta `next_retry_at`,
`dead_lettered` terminal e `telegram_update_receipts`, todos validados em
PostgreSQL 18 real com concorrência, restart e migration reversível. API,
worker, Prometheus, Jaeger, Telegram e as quatro lojas foram validados em
Docker isolado; a tarefa seguinte foi a TASK-050.

A TASK-050 (`DEC-038`) está **concluída**: `docs/PRIVACY.md` inventaria conta,
autenticação, missões, preços, compra, eventos, telemetria e compartilhamentos
necessários. `/privacidade` é resposta fixa sem IA nem sessão. Logs filtram
identificadores pessoais e exceções expõem somente classe segura. Containers
rotacionam `10m × 5`; Prometheus limita 15 dias/2 GB e Jaeger mantém no máximo
10.000 traces voláteis sob 512 MB. `app.privacy` limpa tokens/sessões após
24h/30d e desidentifica conta em transação única, removendo identificadores,
perfil, autenticação, preferências, intenção e textos mutáveis. UUID e fatos
append-only permanecem pseudônimos; PII detectada em histórico imutável aborta
toda a operação antes de mutação. PostgreSQL e Docker reais, canário de
telemetria e Bot API validaram o fluxo sem alterar o proprietário. A próxima
tarefa executada foi a TASK-051. O pipeline terminou com 601 testes e 90,61%
de cobertura.

A TASK-051 (`DEC-039`) está **concluída**: `docs/OPERATIONS.md` consolida a
preparação e operação manual de um único Ubuntu Server headless, sem declarar
produção pronta. API, PostgreSQL, Prometheus, Jaeger e Collector ficam no
loopback por padrão; métricas do worker permanecem internas. Backup PostgreSQL
manual é distinto de disaster recovery e só é considerado validado depois de
restauração em banco limpo. Rollback de código exige compatibilidade com o
schema; downgrade destrutivo nunca é automático. PostgreSQL 18 e o stack real
isolado confirmaram migrations, endpoints, restart, backup `0600`, restauração,
contagem e dado sintético. A tarefa seguinte foi a TASK-052. O pipeline
terminou com 602 testes e 90,61% de cobertura.

A TASK-052 (`DEC-040`) está **concluída**: o pipeline possui uma suíte
permanente e obrigatória contra PostgreSQL 18.4 fixado por digest. O runner
recusa configuração de banco ou ambiente de produção herdados, cria recursos
sintéticos exclusivos em loopback, migra dinamicamente até o único head e
clona um banco limpo por teste. Oito integrações reais cobrem schema/seeds,
missão até consumo concorrente, compra, autenticação, autorização, resiliência
e privacidade. Execução completa repetida, teste individual, falha controlada e
guard fora do runner foram aprovados sem deixar containers ou volumes. A
próxima tarefa seria inicialmente a TASK-053; E2E externo continua
exclusivamente nela. O pipeline terminou com 607 testes rápidos, 90,61% de cobertura e 8
integrações PostgreSQL reais.

O preflight da TASK-053, em 2026-08-09, comprovou a lacuna entre missão ativa,
agenda, providers, histórico e eventos. A TASK-062 (`DEC-041`) foi criada como
requisito do MVP e concluída antes dos E2E: novas missões recebem agenda,
`collection_worker` usa claim curto com `FOR UPDATE SKIP LOCKED`, chama os
quatro providers fora da transação, persiste observações, avalia alertas e
publica eventos por fonte. PostgreSQL 18.4, concorrência real e Docker
Linux/Xvfb com as quatro lojas foram validados. A TASK-053 é a próxima.
O pipeline oficial terminou com 638 testes rápidos, 90,04% de cobertura e 11
integrações PostgreSQL reais.

A TASK-053 obteve `PASS` no E2E externo em 2026-08-09, depois da
disponibilidade por card, do DEC-045 (alertas por `amount`, sem exigir
frete), do DEC-046 (intervalo/stagger) e do DEC-047 (backoff persistente por
fonte). O E2E reproduzível também foi refeito (2/2 aprovados) depois de
corrigir um teste que não considerava o stagger de `DEC-046` na criação da
missão — achado de teste, não de produto. No E2E externo, uma missão real
criada pelo próprio usuário via Telegram real, com as quatro fontes,
produziu 58 observações reais (41 elegíveis), 11 eventos de alvo e 11
notificações Telegram reais entregues sem duplicação; Amazon e Terabyte
100% `AVAILABLE`, Kabum resolveu 3 ofertas via fallback seletivo (top K=3),
Pichau falhou por instabilidade externa isolada (não um `403/429`
confirmado) sem virar `FAIL_INTERNO` e sem acionar o backoff persistente do
DEC-047. A TASK-053 está **concluída**, com fechamento aprovado
explicitamente pelo usuário em 2026-08-09; a condição externa da Pichau
permanece registrada como observação de terceiro, não como bug interno
pendente.

A TASK-054 está **concluída**: fechou `docs/RELEASE_CHECKLIST.md` como
retrato real do repositório (63/63 tarefas, 8/8 critérios objetivos do MVP
atendidos) e publicou o tag Git anotado `v1.0.0` em `origin`, marcando o
commit revisado da V1 — por decisão explícita do usuário, só o tag, sem
deploy real num Ubuntu Server, sem CI/CD e sem GitHub Release pública. O MVP
da V1 está completo; não há próxima TASK do roadmap pendente. Evoluções
(V1.2 em `docs/V1_2.md`, V2 em `docs/BACKLOG.md`) exigem decisão explícita
antes de qualquer TASK nova.

**Atualização 2026-08-09:** a TASK-063 (`DEC-048`, `docs/tasks/TASK-063.md`),
registrada depois de o usuário identificar no Telegram real alertas de
preço possivelmente irrelevantes ao produto pedido (nome da missão em vez
do anúncio real, sem link direto), está **concluída**: classificador de
relevância `MATCH`/`POSSIBLE_MATCH`/`NO_MATCH` por `(mission_id, offer_id)`
(só `MATCH` alerta), correção do bug de `previous` compartilhado entre
missões, `products.display_name`, título/loja/link reais no alerta e
formatação revisada das mensagens principais do Telegram — tudo validado
(pipeline oficial, E2E reproduzível, missão real) e aprovado explicitamente
pelo usuário.

A validação real da TASK-063 revelou um problema separado: a camada
premium da cascata `AdminDevAIProviderManager` (`gemini-3.1-pro-preview`)
teve 0% de sucesso em 248 tentativas reais, sempre `unavailable`/
`quota_exceeded`. Desmembrado para a **TASK-064** (`DEC-049`,
`docs/tasks/TASK-064.md`): auditoria confirmou que o modelo configurado é
oficialmente `preview`, e um teste mínimo mostrou que mesmo um candidato
GA "Pro" (`gemini-pro-latest`) falha com `quota_exceeded` de imediato,
enquanto um modelo GA "Flash" (`gemini-3.5-flash`) responde normalmente —
mais consistente com a chave não ter cota real de nível "Pro" do que com
um problema pontual do modelo escolhido.

**Atualização 2026-08-09/2026-08-10:** o usuário fechou a decisão (`DEC-050`)
sem depender da pergunta sobre faturamento — `USER`, `ADMIN` e `DEV` usam o
mesmo Gemini Flash para as operações automáticas de IA, nenhum nível
Pro/preview entra na cascata, fallback só por disponibilidade
(Flash→Groq). A implementação foi autorizada e **a TASK-064 está
concluída, aprovada explicitamente pelo usuário em 2026-08-10**:
`AdminDevAIProviderManager` colapsado de 3 para 2 camadas,
`gemini_premium_model` removido do config. Validado com pipeline oficial
(752 testes, 90,63% cobertura, 14 integrações reais), E2E reproduzível
(2/2) e chamadas reais contra o stack Docker reconstruído — fallback
Flash→Groq real confirmado e uma coleta representativa (missão
descartável, uma fonte, 20 ofertas novas) obteve 15/20 sucesso em
classificação e em normalização, melhora real sobre a maioria de falhas
da validação original da TASK-063; as falhas restantes do Flash por cota
ficam registradas como condição operacional externa, não como falha da
TASK-064. **A condição que suspendia a release como definitiva está
resolvida** (`docs/RELEASE_CHECKLIST.md`, 65/65) — o tag `v1.0.0`
permanece publicado sem alteração. Ver atualização ao final deste
documento: a `v1.0.0` foi auditada como desatualizada (não continha
TASK-063/TASK-064), levando à tag corretiva `v1.0.1`, hoje já implantada em
produção real.

A TASK-058 (`DEC-015`) originalmente entregou: `create_mission` e
`mission_command` não executam mais direto — ficam encenados em
`User.pending_intent` e só executam após confirmação explícita, descrita em
português para o usuário. Naquela entrega, confirmar/cancelar passava por
`interpret_confirmation_reply`. A correção pontual de 2026-08-15 substituiu
isso por vocabulário local fechado (`sim`/`s`/`1`; `não`/`nao`/`n`/`2`), sem
provider. A validação histórica da TASK-058 cobriu: criar missão sem
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
- Gestão de configuração tipada em `backend/app/core/config.py`; desenvolvimento
  aceita `.env` ignorado ou secret file, enquanto produção exige `*_FILE` e
  mounts mínimos em `/run/secrets`.
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
  que recebe atualizações do Telegram. Somente a descrição enviada após
  `/criar_missao` é traduzida em `Intent`; navegação, seleção, confirmação e
  cancelamento são determinísticos. Há script manual de registro contra a Bot
  API real.
- Autenticação mínima do canal Telegram (TASK-046): transporte autenticado por
  segredo em tempo constante, operações restritas ao chat privado direto e
  bloqueio de conta inativa antes de qualquer efeito.
- Resolução get-or-create de identidade (`get_or_create_telegram_user`) que
  vincula `User.telegram_user_id` — exclusivamente a pessoa do Telegram,
  nunca a conversa — de forma determinística e idempotente, protegida contra
  corrida de criação concorrente por `SAVEPOINT`; o resolvedor não autentica
  sozinho nem implementa login por senha.
- Despacho de comandos de missão pelo webhook: `/criar_missao` abre estado por
  usuário com TTL de 10 minutos e só a descrição seguinte usa IA; criar e
  comandar missão ficam encenados em `User.pending_intent` e só
  executam após confirmação explícita (TASK-058), com resposta síncrona ao
  Telegram (`send_message`); toda `CREATE_MISSION` válida sai `active`.
  Desde a TASK-070, quando o `Intent` não especifica nenhuma loja, o
  webhook não assume mais as quatro fontes-padrão da V1 automaticamente —
  encena um estado pendente à parte perguntando por lista numerada
  própria (`1 Pichau/2 Terabyte/3 Amazon/4 Kabum/5 Todas`), resolvida de
  forma determinística (sem IA), antes de seguir para a confirmação
  normal; erro conhecido de domínio responde `204` com explicação, falha
  inesperada sobe como `500`, nunca mascarada. Primeira dependência FastAPI
  de sessão de banco por requisição (`get_session`).
- Confirmação antes de executar: `backend/app/telegram/confirmation.py`
  resolve localmente `sim`/`s`/`1` e `não`/`nao`/`n`/`2`; resposta ambígua
  mantém a ação pendente e pede novamente, sem IA.
- `/cancelar_missao` (alias digitado `/cancelar-missao`) lista apenas missões
  canceláveis do proprietário, seleciona numericamente, confirma e executa
  `MissionTransition(command=cancel)` sem IA, desativando o agendamento na
  mesma transação.
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
- Limite HTTP, replay/rate limit persistentes do Telegram, retry seguro,
  circuit breakers locais por integração e retry/dead letter append-only de
  eventos (TASK-049).
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
- ADRs, RFCs e 63 tarefas planejadas.

## O que não existe

Além de `users`, `products`, `stores`, `sellers`, `offers`, `audit_entries`,
`missions`, `mission_criteria`, `mission_sources`, `mission_transitions`,
`mission_schedules`, `collection_runs`, `price_observations`, `events`,
`event_consumption_attempts`, `purchase_confirmations`,
`purchase_trail_entries`, `user_credentials`, `user_auth_sessions` e
`credential_action_tokens` e `telegram_update_receipts`, não há outras tabelas
implementadas. Não existem
OAuth, MFA, refresh token, recuperação por e-mail ou outro canal,
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
- Desidentificação remove identificadores diretos, autenticação, preferências e
  textos mutáveis, mas preserva UUID e fatos append-only. PII detectada nesses
  fatos aborta toda a operação; o projeto não chama essa correlação preservada
  de anonimização irreversível (TASK-050/DEC-038).
- Missões nascem em `draft`; alterações de estado e de `state_version` são executadas atomicamente com histórico append-only e versão concorrente.
- Critérios usam busca textual e preço-alvo opcional pareado com moeda; recorrência usa agenda separada com intervalo fixo positivo.
- Eventos usam nomes versionados e payloads mínimos do catálogo; tipos
  desconhecidos e payloads incompatíveis são rejeitados antes da
  persistência ou publicação (TASK-043).
- `events` é append-only: nenhuma linha publicada é alterada ou removida,
  reforçado por trigger no banco (mesmo padrão de `mission_transitions` e
  `audit_entries`). `recorded_at` é gerado exclusivamente pelo PostgreSQL,
  nunca pela aplicação.
- Tentativas de consumo são append-only e at-least-once por consumidor:
  `failed` só volta após `next_retry_at` e abaixo do limite; `succeeded`,
  `skipped` e `dead_lettered` são terminais para o mesmo `consumer_name`;
  reivindicação, processamento e registro compartilham a transação controlada
  pelo chamador (TASK-044/TASK-049).
- O destino Telegram é sempre o chat privado correspondente à pessoa; chats de
  grupo, supergrupo e canal nunca são persistidos automaticamente. Entregas de
  alerta são at-least-once e podem se repetir se a API aceitar a mensagem antes
  de um rollback do banco (TASK-036).
- Update autenticado do Telegram possui no máximo um recibo terminal. Recibo
  `accepted` e efeitos fazem commit ou rollback juntos; replay e rate limit
  retornam `204` sem repetir domínio, IA ou consumir cota (TASK-049).
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
- Módulos da aplicação acessam IA somente por `AIProviderManager`; USER, ADMIN e
  DEV usam o mesmo Gemini Flash (`Settings.gemini_model`) para as operações
  automáticas de IA, com Groq como fallback só de disponibilidade quando
  configurado (opcional, TASK-059) — nenhum nível Gemini Pro/preview participa
  da cascata (TASK-064/`DEC-050`). Papel continua sendo só permissão/
  autorização, nunca escolha de modelo. OpenAI, Claude e usuário pago ficam
  para a V2 — o Groq é fallback interno de infraestrutura, nunca escolha
  exposta ao usuário. Credenciais nunca são versionadas.

**Atualização 2026-08-10:** auditoria confirmou que a tag `v1.0.0`
(`85b56c6`) nunca foi movida e não contém as correções da TASK-063 nem da
TASK-064 — checklist `65/65` descrevia o código corrente, não o conteúdo
tagueado. Por decisão do usuário (`DEC-051`), `v1.0.0` permanece **intocada**
como marco histórico; a tag corretiva **`v1.0.1`** (`578dc29`, inclui
TASK-063 e TASK-064) passou a ser a referência de release atual.
`docs/PRODUCTION_SETUP.md` foi escrito como manual completo de instalação em
Ubuntu Server a partir dela. A **`v1.0.1` foi implantada em um servidor de
produção real** nesta mesma sessão: os 7 serviços do `compose.yaml` sobem e
ficam saudáveis, as 26 migrations foram aplicadas até `20260809_0004`
(head), o webhook do Telegram foi registrado sobre uma URL HTTPS pública
real (túnel próprio do operador, sem CI/CD nem reverse proxy dedicado — a
V1 não define essa infraestrutura), cadastro/senha/login e criação de
missão por texto livre foram validados ao vivo, e o proprietário foi
promovido a `DEV` pelo mesmo procedimento manual documentado na seção 9 do
manual. Um problema real de implantação foi encontrado e corrigido durante
o processo: os containers da aplicação rodam como usuário não-root (UID 999
dentro da imagem), e os arquivos de `.secrets/` inicialmente ficaram com
dono do usuário do host — ilegíveis para o container. Corrigido só com
permissão de arquivo no servidor (`chown` para o UID do container), sem
tocar em `compose.yaml`, `Dockerfile` nem em nenhum código — não acontecia
em desenvolvimento porque o Docker Desktop no Windows não aplica
UID/permissão POSIX real em bind mounts como um host Linux real aplica.

Depois da implantação, o usuário registrou, só como planejamento (nenhuma
TASK criada, nenhum código alterado) seis novos itens, divididos em dois
documentos separados para não confundir as versões (`DEC-059`):
**`docs/V1_0_2.md`** (release corretiva `v1.0.2`) ganhou edição de missão
existente, categorias numeradas no `/cadastro` e pré-lista de preços
encontrados sem IA (um preço por loja) — `DEC-057`/`DEC-055`/`DEC-058`.
**`docs/V1_2.md`** (evolução funcional V1.2) ganhou redução de
`PriceObservation` redundante (gravar só mudança material de estado,
nunca apagar histórico já gravado), Magalu como quinta loja, e comparação
de menor preço histórico externo/interno estilo Steam Inventory Helper —
a mesma pré-lista da `v1.0.2`, com IA por cima — mais pesquisa de ofertas
em lives (YouTube e Shopee Live) — `DEC-053`/`DEC-054`/`DEC-056`. A ordem
de versões da V1 continua: `v1.0.1` (atual, em produção) → `v1.0.2`
(`docs/V1_0_2.md`, corretiva, sem funcionalidade nova exceto três
exceções já sinalizadas explicitamente) → V1.2 (`docs/V1_2.md`, evolução
funcional) → V2.

**Atualização 2026-08-10 (2):** por pedido explícito do usuário, a
`v1.0.2` entrou em **planejamento ativo**: os 5 itens de
`docs/V1_0_2.md` foram convertidos em propostas de TASK (TASK-065 a
TASK-069, uma por responsabilidade), com numeração, nome, objetivo,
dependências e ordem recomendada apresentados ao usuário para aprovação.
A implementação segue item por item, cada uma só após aprovação explícita,
pelo workflow oficial (TASK → implementação → validação → commit →
aprovação → push). A `v1.0.1` em produção não foi tocada por este
planejamento.

**Atualização 2026-08-10 (3):** aprovada e concluída a **TASK-065**
(`docs/tasks/TASK-065.md`, item 1 da `v1.0.2`) — auditoria reconfirmou que
`AISHOPPING_GEMINI_MODEL`/`AISHOPPING_GROQ_MODEL` seguem sem propagação
real em `compose.yaml` (zero ocorrências nos 7 serviços); removidas de
`.env.example` (raiz), `backend/.env.example` e do `backend/.env` local
(limpeza não versionada, sem expor valores); `docs/DEPENDENCIES.md` e
`docs/PRODUCTION_SETUP.md` atualizados para não anunciar essas variáveis
como configuráveis. `Settings.gemini_model`/`Settings.groq_model`
(`backend/app/core/config.py`) e `manager.py` **não foram alterados** — a
cascata Flash→Groq (`DEC-050`) e a produção da `v1.0.1` seguem intocadas.

**Atualização 2026-08-10 (4):** aprovada e concluída a **TASK-066**
(`docs/tasks/TASK-066.md`, item 2 da `v1.0.2`) — a auditoria encontrou que
a política parcial de restart já era contraditória
(`collection_worker`/`telegram_notifier` com `unless-stopped` dependem de
`database`, que não tinha a política, então o auto-restart deles já era
parcialmente inútil após reboot real). O usuário aprovou explicitamente
aplicar `restart: unless-stopped` aos 5 serviços restantes (`database`,
`api`, `otel-collector`, `prometheus`, `jaeger`), deixando os 7 serviços
consistentes. Validado com o pipeline oficial completo e com um teste real
isolado (não produção): `database`/`jaeger` subidos localmente, crash
interno simulado (`docker exec ... kill -9 1`, diferente de `docker
stop`/`kill` no nível do Engine, que `unless-stopped` trata como parada
intencional), recuperação automática confirmada em segundos. Produção da
`v1.0.1` intocada.

**Atualização 2026-08-10 (5):** aprovada e concluída a **TASK-067**
(`docs/tasks/TASK-067.md`, item 4 da `v1.0.2`) — pesquisa ao vivo (Browser)
da taxonomia real de categorias de Kabum, Pichau, Terabyte e
Amazon.com.br, consolidada por critério objetivo (categoria presente em
pelo menos 2 das 4 lojas, excluindo o catálogo genérico exclusivo da
Amazon) em 15 categorias + "Todas". O usuário aprovou a lista sem
alterações. `backend/app/users/registration.py` passou a usar o mesmo
padrão de lista numerada de `favorite_stores` para
`preferred_categories`; `preferred_categories` continua sem consumidor
além de metadado (só desidentificação, `backend/app/privacy/service.py`).
Validado com pipeline oficial (756 testes, 90,65% cobertura,
`registration.py` a 100%) e o teste de fluxo completo real do `/cadastro`
via `receive_telegram_webhook`; sem round-trip ao vivo contra a API do
Telegram, por ser mudança de vocabulário fechado sem alterar a mecânica
do webhook já validada em produção. Produção da `v1.0.1` intocada.

**Atualização 2026-08-10 (6):** aprovada e concluída a **TASK-068**
(`docs/tasks/TASK-068.md`, item 5 da `v1.0.2`) — pré-lista informativa
sem IA, disparada uma única vez por missão quando toda `MissionSource`
já teve pelo menos um `CollectionRun` terminal (sucesso ou falha). O
usuário revisou o desenho inicial ("1 preço por loja mostrando todas")
durante a TASK e pediu uma versão diferente: comparar 1 oferta `MATCH`
candidata por loja e mostrar as 2 mais baratas (nunca 2 da mesma loja),
com um texto deixando claro que a busca continua, mais um mecanismo de
correção — no máximo uma mensagem, se uma coleta posterior encontrar
algo mais barato que a base já enviada. Reaproveita a classificação
`MATCH` já calculada pela TASK-063 (nenhuma IA nova); comparação por
`PriceObservation.amount` (preço do produto, **nunca** `total_amount`)
— **correção pedida pelo usuário antes da publicação**: o frete ainda
não é confiável/comparável entre as 4 lojas nesta V1, então a base de
ranqueamento não pode incluí-lo; a mensagem final deixa explícito que o
valor mostrado não inclui frete. Coerente com `evaluate_price_alerts`/
`DEC-045`, que também usa só `amount` (pelo mesmo motivo de fundo),
embora para a *mesma* oferta ao longo do tempo, não para ranquear
ofertas diferentes de lojas diferentes num instante como a pré-lista
faz. Dois `EventType` novos com payload autocontido
(`MissionPrelistReadyPayload`/`MissionPrelistErrataPayload`), consumer
Telegram dedicado (`telegram_prelist_v1`), sem consultar
`notify_price_decreases`/`notify_target_reached` (TASK-037). Validado com
pipeline oficial (771 testes, 90,49% cobertura, migration head
`20260810_0001`, 16 integrações PostgreSQL reais) e testes de integração
reais cobrindo as 3 rodadas do cenário completo (pré-lista com 1 oferta,
correção única, sem segunda correção) e um cenário dedicado onde
`amount` e `total_amount` discordam sobre qual oferta é mais barata,
provando que a implementação ranqueia pela base correta.
`evaluate_price_alerts`, preferências de queda/alvo e a semântica
MATCH/POSSIBLE_MATCH/NO_MATCH da TASK-063 intocadas. Produção da
`v1.0.1` intocada.

**Atualização 2026-08-10 (7):** aprovada e concluída a **TASK-069**
(`docs/tasks/TASK-069.md`, item 3 da `v1.0.2`) — última TASK planejada da
`v1.0.2`. Novo `IntentKind.EDIT_MISSION` (não um `MissionCommand` novo —
edição de critérios não muda `status`) edita `MissionCriteria.target_amount`/
`target_currency` e/ou as fontes selecionadas (`MissionSource`) de uma
missão já criada, sem precisar recriá-la. Só missões `PAUSED` são
editáveis (decisão explícita do usuário, mais estrita que a proposta
inicial). Durante o desenho, o usuário acrescentou uma instrução: se a
missão estiver `ACTIVE`, o bot não rejeita — pergunta se o usuário quer
pausar agora (mesmo par confirmar/cancelar "1"/"2" já usado em toda
confirmação); confirmado, pausa de verdade (`transition_mission` com
`PAUSE`) e orienta reenviar o pedido via novo comando `/editar-missao`;
pausar e editar nunca acontecem como um único passo automático. A missão
permanece `PAUSED` depois de editada — só volta a coletar quando o
usuário retomar. `find_due_schedules` já filtra `Mission.status ==
ACTIVE`, então uma missão pausada nunca é reivindicada por
`claim_due_collections` — editar é estruturalmente seguro, sem coleta em
andamento para coordenar. Preço-alvo pode ser limpo (par `NULL`/`NULL`);
remover uma loja apaga só a linha de `MissionSource` — o histórico
(`CollectionRun`/`PriceObservation`) daquela loja nunca é apagado
(confirmado por teste de integração real dedicado). Nenhuma migration
necessária; nenhuma IA nova — reusa `IntentInterpreter` (vocabulário
fechado estendido) e `interpret_confirmation_reply` já existentes.
Validado com pipeline oficial (815 testes, 90,69% cobertura, migration
head `20260810_0001` sem alteração, 21 integrações PostgreSQL reais).
Com esta TASK, os 5 itens do **planejamento original** de
`docs/V1_0_2.md` estão implementados e validados; produção da `v1.0.1`
intocada; nenhuma tag `v1.0.2` criada.

**Atualização 2026-08-10 (8):** logo depois de aprovar a publicação da
TASK-069, o usuário ampliou o escopo da `v1.0.2` (`DEC-060`) com mais
dois itens, registrados em `docs/V1_0_2.md` como 6 e 7, **sem
implementação e sem TASK aberta** (pedido explícito de não implementar
agora, só registrar): impedir `/cadastro` para um usuário já
autenticado/logado; e, quando uma missão for criada sem nenhuma loja
informada, perguntar as lojas por lista numerada (`1 Pichau`,
`2 Terabyte`, `3 Amazon`, `4 Kabum`, `5 Todas`). **A `v1.0.2` continua
aberta** — só o planejamento original de 5 itens está concluído.
Nenhuma tag `v1.0.2` criada; produção da `v1.0.1` intocada.

**Atualização 2026-08-11:** aprovada e concluída a **TASK-070**
(`docs/tasks/TASK-070.md`, item 7 da `v1.0.2`). `CREATE_MISSION` sem
loja nenhuma informada não assume mais as quatro fontes da V1
automaticamente — encena um novo estado pendente
(`await_create_mission_sources`, preservando `search_query`/
`target_amount`/`target_currency`) e pergunta por lista numerada própria
(`1 Pichau/2 Terabyte/3 Amazon/4 Kabum/5 Todas`, ordem diferente da do
`/cadastro`, que não foi alterado). A resposta é interpretada de forma
determinística, sem IA (`parse_numbered_store_selection`,
`backend/app/telegram/confirmation.py`), validando a entrada por
completo — qualquer token não reconhecido invalida a resposta inteira
(nunca aceita parcialmente, ex.: `"1,9"` é inválido mesmo o `"1"`
existindo); repetição é deduplicada; misturar `"5"` com outro número
ainda resulta em todas. Só depois de uma seleção válida a missão fica
encenada como `create_mission`, seguindo para a confirmação sim/não já
existente (TASK-058) — a missão nunca é criada antes disso, e a resposta
numérica nunca passa pelo `IntentInterpreter` de novo.
`_DEFAULT_V1_SOURCE_CODES` (`backend/app/missions/service.py`) foi
preservado sem alteração, porque `backend/scripts/validate_collection_worker.py`
e um teste unitário ainda dependem dele — só o fluxo do webhook deixou
de exercitá-lo. Validado com pipeline oficial completo. Com esta TASK, o
item 7 da `v1.0.2` está concluído; o item 6 (bloquear `/cadastro` para
usuário já autenticado) continua registrado e pendente, sem TASK aberta
— **a `v1.0.2` continua aberta**. Nenhuma tag `v1.0.2` criada; produção
da `v1.0.1` intocada.

**Atualização 2026-08-11 (2):** concluída a **TASK-071** — não é item da
`v1.0.2`, pedido explícito do usuário depois de uma simulação da edição
de missão (TASK-069) revelar um risco real: `IntentParameters.sources`
sempre foi tratado como a lista completa final de lojas, mas a IA nunca
sabe quais lojas a missão já tem, então "adiciona kabum e terabyte" sem
repetir a loja já selecionada fazia a confirmação **remover** essa loja
sem o usuário perceber facilmente. Decisão: `/editar-missao` virou um
**menu guiado e 100% determinístico** — resolve qual missão (sem IA:
`PAUSED` única auto-seleciona, mais de uma lista numerada para escolher,
sem nenhuma pausada reaproveita o pedido de pausa já existente para a(s)
`ACTIVE`), depois `1 Lojas`/`2 Preço-alvo`; lojas ganha `1
Adicionar`/`2 Remover` (mostra só as que faltam ou só as vinculadas,
nunca permite zerar todas); preço-alvo pede o valor direto (`0` remove o
alvo). Todos os caminhos convergem para o mesmo payload
`stage_edit_mission`/`describe_edit_mission` (TASK-069, sem alteração) —
a confirmação final sim/não usava então `interpret_confirmation_reply`;
a correção pontual de 2026-08-15 tornou essa confirmação determinística e
local. **O
caminho antigo (editar por texto livre) foi desativado por decisão
explícita do usuário** — `IntentKind.EDIT_MISSION` continua existindo no
vocabulário, mas o webhook só responde orientando a usar
`/editar-missao`, sem executar nada. `edit_mission_criteria` (serviço),
`stage_pause_for_edit`/`describe_pause_for_edit` e
`parse_numbered_store_selection` (TASK-070) foram totalmente
reaproveitados, sem nenhuma alteração. Validado com pipeline oficial
completo (876 testes, 91,00% cobertura, 21 integrações PostgreSQL
reais). Nenhuma tag `v1.0.2` criada; produção da `v1.0.1` intocada;
nenhuma outra TASK iniciada.

**Atualização 2026-08-11 (3):** concluída a **TASK-072** (item 6 da
`v1.0.2`, `docs/tasks/TASK-072.md`) — último item pendente da versão.
`/cadastro` passa a ser bloqueado quando `has_active_session` é `True`,
respondendo com mensagem fixa ("✅ Você já está cadastrado e autenticado
neste Telegram.") sem alterar `registration_step` nem nenhum campo já
salvo; sem sessão ativa, o comportamento é idêntico ao de antes. Durante
o desenho, o usuário ampliou a preocupação para username duplicado entre
contas e um telefone com mais de uma conta — uma auditoria dedicada
mostrou que essas duas últimas **já eram estruturalmente garantidas**
(`User.telegram_user_id` e `User.username` já têm constraint `UNIQUE` no
banco; `get_or_create_telegram_user` é seguro contra corrida; a sessão é
sempre resolvida a partir do `telegram_user_id` recebido, nunca de um
dado informado pelo usuário — não existe caminho para uma conta
autenticar através da identidade de outra pessoa), sem nenhuma mudança
de código necessária para esses dois pontos. A única lacuna real era de
UX: o passo `username` do `/cadastro` nunca consultava o banco antes de
aceitar, então duas pessoas escolhendo o mesmo nome ao mesmo tempo
faziam a segunda travar silenciosamente mais adiante (a escrita falhava
na constraint sem nenhuma mensagem clara). Corrigido com
`_ensure_username_available` (`backend/app/users/registration.py`) — uma
checagem antecipada, melhoria de UX que **não substitui** a constraint
`UNIQUE`, que continua sendo a proteção real contra corrida. Validado
com pipeline oficial completo (880 testes, 91,06% cobertura, 21
integrações PostgreSQL reais). **Com esta TASK, os 7 itens da `v1.0.2`
estão implementados e validados — todo o escopo registrado desta versão
está concluído.** Nenhuma tag `v1.0.2` criada ainda; publicação final
pendente de decisão explícita do usuário; produção da `v1.0.1` intocada;
nenhuma outra TASK iniciada.

**Atualização 2026-08-11 (4):** a tag `v1.0.2` (`ea653b8`) foi criada e
publicada em `origin`; produção passou por deploy controlado, em fases,
da `v1.0.1` (`578dc29`) para a `v1.0.2` — backup lógico do PostgreSQL
antes de qualquer alteração, missão de teste antiga retirada por
transição de estado real (`active → cancelled`, com auditoria em
`mission_transitions`), migration aplicada até `20260810_0001`, os 7
serviços validados saudáveis com `restart: unless-stopped`. Durante a
validação real em produção, o usuário identificou que `/cadastro` não
bloqueava um cadastro já concluído quando a sessão caía — investigação
confirmou que era o desenho aprovado da TASK-072 (bloqueio só por
sessão ativa), não um bug. Registrada e concluída a **TASK-073**
(`docs/tasks/TASK-073.md`), item único da `v1.0.3`: `/cadastro` agora
também bloqueia quando o cadastro já está concluído
(`registration_step is None` e `username` preenchido), mesmo sem
sessão ativa, direcionando para `/entrar`/`/recuperar`;
cadastro em andamento não foi afetado. Validada com pipeline oficial.
Tag `v1.0.3` (`6fa5e13`) criada, publicada e implantada em produção
na mesma sessão: backup lógico prévio, checkout da tag, build, sem
migration nova (head `20260810_0001` inalterado), `api`/
`collection_worker`/`telegram_notifier` recriados com a imagem nova
— `database`, `jaeger`, `otel-collector` e `prometheus` intocados.
`/health`/`/ready` `200`; `restart: unless-stopped` confirmado nos 7
serviços; dados de produção preservados.

**Atualização 2026-08-11 (5):** durante o teste em produção, uma missão
criada só com "9950x3d" mostrou que `search_query` (usado literalmente
como termo de busca em cada loja) nunca corrigia digitação nem
completava marca/modelo. Registrada e concluída a **TASK-074**
(`docs/tasks/TASK-074.md`): prompt do `IntentInterpreter` ajustado para
corrigir erro óbvio ("logitek" → "logitech") e completar marca/modelo
reconhecível ("9950x3d" → "ryzen 9 9950x3d"), sem abrir espaço para
inventar especificação não mencionada — confirmado por regressão
("mouse bom e barato" → `search_query: "mouse"`). Validada com pipeline
oficial e chamadas reais contra o perfil `ADMIN` (nunca `USER`). O caso
de "zero resultados silencioso" para produto inexistente (ex.:
"9951x3d") fica registrado como lacuna conhecida, fora do escopo desta
TASK.

**Atualização 2026-08-11 (6):** a mesma missão de teste revelou que uma
busca ampla ("ryzen 9 9950x3d") gerava dezenas de candidatos
irrelevantes por loja, estourando a cota de IA (Gemini + Groq ao mesmo
tempo) na classificação de relevância. Registrada e concluída a
**TASK-075** (`docs/tasks/TASK-075.md`): `IntentInterpreter` (mesma
chamada, sem chamada extra) passa a devolver `search_query` canônico
completo (tipo primeiro, ex. "Processador AMD Ryzen 9 9950X3D") e um
novo campo estruturado `model` (`mission_criteria.model`, migration
`20260811_0001`, nullable, sem afetar missões existentes). Nova camada
determinística em `_persist_success` (`app/collection/orchestration.py`)
roda uma única vez, antes de qualquer persistência/IA: filtro de modelo
(tolerante a separador, distingue `RTX 4070`/`RTX 4070 Ti`/`RTX 4070 Ti
SUPER` sem falso-positivo em `OC`) e filtro de bundle/PC completo,
sempre conservadores (ambíguo segue pra relevância existente). Regra
exclusiva da Amazon: entre vendedores confirmados pelos filtros (não
por ASIN — vendedores diferentes do mesmo produto vêm em ASINs
diferentes, confirmado ao vivo), mantém só a oferta de menor preço,
com gate obrigatório (`model` precisa existir; sem ele, nunca escolhe
"a mais barata"). Kabum ganhou `facet_filters` de produto
vendido/entregue pela própria loja. `product_type` estruturado avaliado
e descartado (redundante frente ao filtro de bundle já existente).
Validada com pipeline oficial completo e chamadas reais contra o perfil
`ADMIN` (`"quero uma 4070 ti"` → `model: "RTX 4070 Ti"`; `"procura um
9800x3d"` → família correta `Ryzen 7`).

**Atualização 2026-08-11/12 (7):** publicado em teste controlado (sem
tag) o commit da TASK-075, o usuário validou uma missão real pelo
Telegram e a pré-lista só mostrou Kabum e Amazon. Investigação (logs +
banco) confirmou que a Terabyte teve `collection_run` `succeeded` sem
oferta persistida (produto genuinamente esgotado) e a Pichau falhou
com `ProviderNavigationError`. Causa raiz confirmada: `_collect_once`
reaproveitava `AISHOPPING_EXTERNAL_HTTP_TIMEOUT_SECONDS` (10s) como
timeout de navegação do Playwright, insuficiente para o carregamento
real da Pichau (~20-38s até `domcontentloaded`). Novo
`AISHOPPING_BROWSER_NAVIGATION_TIMEOUT_SECONDS` (default `45`,
exclusivo do `collection_worker`) desacopla os dois timeouts — mas o
reteste isolado mostrou que 45s por si só não resolvia de forma
confiável, levando a um segundo diagnóstico: `wait_until="commit"` +
espera pelo card real ficou pronta em 9-12s, contra 22-38s do
`domcontentloaded` (atrasado por scripts de terceiros/analytics
alheios ao conteúdo útil). Identificado ao vivo o texto estável do
estado de "zero resultados" (`"Nenhum produto encontrado"`). Nova
extensão opt-in em `PlaywrightStoreProvider`
(`navigation_wait_until`, `empty_result_locator`, padrão inalterado
para as demais lojas); `PichauProvider` passa a navegar com `commit` e
aguardar o primeiro entre card real e estado vazio, devolvendo coleta
válida com zero ofertas em vez de `provider_unavailable` quando
aplicável. Validado com pipeline oficial (915 testes, 91,22%
cobertura) e reteste isolado real (3 buscas reais + 1 vazia, todas na
1ª tentativa). **Validação funcional real em produção** (missão
`"Processador AMD Ryzen 7 5800X3D"`, iniciada manualmente pelo usuário
via Telegram): as 4 lojas concluíram com sucesso
(`collection_claimed=4`, `collection_succeeded=4`,
`collection_failed=0`) — Amazon R$ 2.184,99, Kabum R$ 2.299,99, Pichau
R$ 2.489,99 (10,48s), Terabyte R$ 2.699,99 (12,48s); pré-lista mostrou
corretamente só Amazon e Kabum, as duas mais baratas, por desenho do
`_maybe_publish_prelist_ready` (top-2), não por falha das outras
duas. Publicada como release `v1.0.5`, consolidando a TASK-075 e esta
correção.

**Atualização 2026-08-11/12 (8):** por pedido explícito do usuário, a
`v1.0.6` entrou em **planejamento ativo** — dois itens, cada um com sua
própria proposta de TASK (`docs/tasks/TASK-076.md`,
`docs/tasks/TASK-077.md`), numeração confirmada como a próxima livre no
repositório (TASK-075 era a última existente). **TASK-076**
(observabilidade): achada durante o próprio diagnóstico da correção da
Pichau na `v1.0.5` — a causa raiz só pôde ser confirmada reproduzindo a
falha isoladamente porque `logger.warning("collection_source_failed",
...)` (`app/collection/orchestration.py::_process`) descarta o objeto
da exceção original (tipo, status, traceback) logo depois de reduzi-lo a
`failure_code`, mesmo com essa informação ainda em escopo no código.
Investigação confirmou também que o `JsonFormatter`
(`app/core/logging.py`) hoje nunca serializa traceback para nenhum
logger do projeto, e que campos `extra` com nomes contendo certas
substrings (`"url"`, `"query"`, etc.) são descartados silenciosamente
pelo redator automático — achados que moldam o desenho técnico proposto
no documento da TASK. **TASK-077** (Amazon): confirmado que nenhum
provider do projeto jamais preenche `seller_external_id`, então nenhuma
linha de `Seller` é criada hoje e toda oferta da Amazon é tratada como
"retailer" (índices de identidade "marketplace" do schema existem mas
são código morto); o pedido é só uma classificação binária
(`amazon`/`marketplace_partner`/`unknown`) a partir do texto de
vendedor já capturado no card de busca, sem catalogar terceiros e sem
mudar a regra de menor preço exclusiva da Amazon (TASK-075). Nenhuma
das duas TASKs foi implementada — só planejadas, com decisões
arquiteturais explicitamente marcadas como pendentes de aprovação do
usuário em cada documento (onde persistir a classificação da TASK-077;
se estender o `JsonFormatter` compartilhado ou só o ponto de log da
TASK-076). Produção da `v1.0.5` não foi tocada por este planejamento.

**Atualização 2026-08-12 (9):** incidente operacional em produção, sem
nenhuma mudança de código/aplicação — puramente infraestrutura do
servidor. O bot ficou fora do ar duas vezes na mesma manhã, por causas
diferentes:

1. **Travamento completo do sistema operacional** (`cesar-server`) por
   volta de 03:53, sem painc nem OOM registrado — o journal simplesmente
   para de logar, e o boot seguinte confirma desligamento sujo
   (`systemd-journald: ... corrupted or uncleanly shut down`).
   Investigação (`lspci`, `lsmod`, journal de todo boot) encontrou o
   driver `nouveau` (GPU NVIDIA GeForce GT 610) falhando repetidamente em
   **todo** boot (`failed to create ce channel, -22`), consistente com
   histórico anterior do usuário de travamentos ao usar interface
   gráfica nessa mesma placa. Confirmado que o acesso remoto (RDP via
   `xrdp`) já usa um driver X virtual próprio (`xrdpdev`,
   `/etc/X11/xrdp/xorg.conf`) com `DRMAllowList "i915 radeon"` —
   nouveau já estava excluído dali, então desabilitá-lo não afeta o RDP.
   **Correção**: `nouveau`/`nvidiafb` desabilitados via
   `/etc/modprobe.d/blacklist-nouveau.conf` (`blacklist` +
   `options nouveau modeset=0`), `initramfs` reconstruído, reboot real
   validado — primeiro boot dessa máquina sem nenhum erro de driver de
   vídeo no journal. GDM (login gráfico local) já estava desabilitado,
   então nada muda no uso real; só a saída de vídeo acelerada por essa
   GPU deixa de existir (console básico via framebuffer do firmware
   continua disponível).
2. **Tailscale Funnel não se re-registrou publicamente após o reboot** —
   `tailscale funnel status` local reportava "on", mas requisições
   externas genuínas (testadas forçando conexão direta ao IP público
   real via `curl --resolve`, contornando o atalho do MagicDNS que fazia
   testes anteriores parecerem bons) davam timeout total. Isso deixou o
   webhook do Telegram inacessível de fora mesmo com todos os 7 serviços
   saudáveis — `pending_update_count` da API do Telegram confirmou
   mensagens presas sem entrega. **Correção**: `tailscale funnel reset`
   + reaplicação (`tailscale funnel --bg 8000`) resolveu imediatamente
   (`pending_update_count` voltou a 0).
3. **Prevenção**: novo timer systemd
   `telegram-funnel-healthcheck.timer` (a cada 5 min) testa o caminho
   público real do Funnel (mesma técnica de `--resolve` via DNS
   público) e, se falhar duas checagens seguidas, reinicia o
   `tailscaled` e reaplica o Funnel sozinho — com limite de 1 restart a
   cada 10 min para não entrar em loop. Não cobre o travamento do
   sistema operacional em si (item 1) nem substitui monitoramento/alerta
   — só evita que uma recorrência do item 2 específico fique sem
   correção até alguém notar manualmente.

Nenhuma mudança em `docs/tasks/`, `CHANGELOG.md` de release ou no código
do repositório da aplicação — só `docs/PROJECT_CONTEXT.md` e
`docs/CHANGELOG.md` registram o incidente, e `docs/PRODUCTION_SETUP.md`
passa a documentar o timer e o blacklist como parte da configuração
esperada do servidor.

**Atualização 2026-08-16 — TASK-084:** concluída a entrega visual individual
de ofertas no Telegram. `Offer.image_url` preserva a última mídia válida; links
`/r/{token}` são opacos, persistentes e resolvem `Offer.url` com validação do
host da Store; checkpoints são isolados por consumidor/evento/oferta/parte e
somente gravados após sucesso confirmado. Rejeição específica de mídia faz
fallback imediato para texto. Os seletores foram congelados após investigação
real isolada das quatro lojas. Validado com 1.200 testes não-integração e 32
integrações PostgreSQL 18.4; TASK-077 permanece a única TASK pendente.

**Atualização 2026-08-12 (10):** durante a validação real da missão
"cadeira gamer", duas coletas (kabum, amazon) ficaram presas em
`running` para sempre — investigação confirmou que **o
`collection_worker` inteiro travou** (uma segunda missão, 9950X3D, que
rodava com sucesso a cada 30 min, também parou no mesmo momento).
Evidência preservada do processo travado: 15 processos filhos zumbis
(14 Chromium + 1 Xvfb), nenhuma query ativa no banco, nenhuma conexão
com Gemini/Groq, 4 threads em espera genérica do kernel, sem nenhuma
exceção registrada. `py-spy` não conseguiu capturar o stack real do
Python (seccomp do Docker bloqueia `ptrace`; não instalado no host) —
limitação registrada, não contornada ainda. Hipótese forte e **ainda
não comprovada**: falta de init real como PID 1 do container
interferindo no rastreamento de saída de subprocessos Chromium pelo
`asyncio`. Registrada **TASK-079**
(`docs/tasks/TASK-079.md`) como **primeira prioridade de implementação
da `v1.0.6`**, à frente de TASK-076/077/078 (nenhuma renumerada, só a
ordem de execução muda) — usuário autorizou trabalhar diretamente em
produção para diagnóstico/validação (aplicação sem uso normal por
usuários neste momento), com preservação explícita de banco, dados,
secrets e do ponto de rollback (`v1.0.5`). Exige diagnóstico completo
(auditoria de lifecycle Playwright no código, instrumentação temporária,
reprodução controlada sem init, comparação objetiva com `init: true`)
antes de declarar causa raiz confirmada ou implementar qualquer
correção — investigação em andamento.

**Atualização 2026-08-15:** o repositório autoritativo para continuidade é
`C:\app\AIShoppingAgent`, no Windows Server. O pacote pontual que consolidou
`/recuperar`, removeu `/senha` do fluxo público e tornou determinísticos os
comandos e confirmações do Telegram foi commitado localmente em `fd68939`, sem
push, rebuild ou deploy; os containers ativos continuam usando a imagem
anterior. A validação encontrou novamente o drift preexistente do
`alembic check` em `mission_command_values`, `store_source_type_values` e
`user_role_values`. A pendência está descrita em
`docs/ALEMBIC_CHECK_ISSUE.md`; o estado de retomada está em
`docs/HANDOFF_SERVER_2026-08-15.md`. Nenhuma correção do Alembic e nenhuma
nova TASK foram iniciadas.
