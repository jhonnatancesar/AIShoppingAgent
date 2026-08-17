# Instruções para agentes

## Leitura obrigatória

Antes de qualquer tarefa, leia integralmente `CLAUDE.md`, todos os documentos em `docs/`, `docs/ROADMAP.md` e o arquivo da tarefa em `docs/tasks/`.

## Regras de execução

- Execute exclusivamente a tarefa solicitada.
- Não antecipe funcionalidades, integrações ou infraestrutura previstas para tarefas futuras.
- Atualize `docs/PROJECT_CONTEXT.md` e `docs/CHANGELOG.md` ao concluir uma tarefa.
- Mantenha decisões arquiteturais em `adr/` e propostas relevantes em `rfc/`.
- Preserve todo o histórico de preços; descarte somente com política explícita aprovada.
- Todo acesso futuro a provedores de IA deve passar pelo AI Provider Manager.

## Governança de novas funcionalidades

Sempre que o usuário sugerir uma nova funcionalidade, analise primeiro o impacto no escopo, dependências, arquitetura, dados, operação e complexidade do projeto. A funcionalidade nunca deve ser implementada imediatamente.

Classifique a sugestão em exatamente uma categoria:

- Implementar agora
- Nova TASK do MVP
- Backlog
- Versão futura
- Out of Scope
- Rejeitada

Após classificar, registre a decisão em `docs/DECISION_LOG.md` e atualize automaticamente a documentação correspondente. Proteja sempre o escopo definido em `docs/MVP.md` e evite aumento de complexidade desnecessário. A classificação não substitui a necessidade de uma TASK explicitamente solicitada para implementar qualquer funcionalidade.

## Workflow oficial e permanente de execução de TASKs

Este é o workflow obrigatório para toda TASK solicitada, em qualquer conversa futura. Ele deve ser iniciado automaticamente, sem necessidade de nova solicitação do usuário.

### 1. Preparação

Antes de qualquer alteração, ler integralmente:

- `AGENTS.md`
- `CLAUDE.md`
- `docs/PROJECT_CONTEXT.md`
- `docs/ROADMAP.md`
- `docs/CHANGELOG.md`
- `docs/MVP.md`
- `docs/BACKLOG.md`
- `docs/OUT_OF_SCOPE.md`
- `docs/DECISION_LOG.md`
- `docs/DEPENDENCIES.md`
- o arquivo da TASK solicitada em `docs/tasks/`

Em uma nova máquina, comparar também as dependências descritas em `docs/DEPENDENCIES.md` com o ambiente disponível. Instalar somente dependências ausentes ou incompatíveis. O usuário concede autorização permanente para baixar e instalar o que for necessário para executar e validar o projeto; ainda devem ser respeitadas confirmações obrigatórias do sistema operacional, segurança, licenças e o princípio do menor impacto.

Use exclusivamente a instalação oficial do Python da máquina para instalar dependências e executar o projeto. Nunca instale pacotes nem execute validações com runtimes Python internos do Codex, de plugins, caches ou de outras ferramentas hospedeiras. Qualquer alerta do antivírus interrompe imediatamente a execução; o objeto não deve ser restaurado nem incluído em exceções sem investigação e autorização explícita.

### 2. Validação

Antes de escrever código, verificar se a TASK é consistente com a arquitetura, pertence ao MVP, possui dependências satisfeitas e não conflita com outras TASKs. Havendo qualquer inconsistência, interromper a implementação, explicar o problema e propor a correção.

Executar também um preflight completo dos recursos necessários para desenvolver e validar a TASK de forma real. Identificar antecipadamente credenciais, chaves de API, contas, permissões, serviços externos, banco de dados, contêineres, navegador, sistema operacional, hardware, ferramentas e dados de teste exigidos pelo fluxo. Verificar de modo seguro apenas a presença e a validade do que já estiver configurado, sem exibir segredos.

Se faltar algo que dependa do usuário, informar antes da implementação exatamente o que é necessário, onde obter, como configurar localmente e qual teste será executado. Segredos devem ser inseridos pelo usuário em `.env`, secret store ou variável de ambiente ignorada pelo Git e nunca enviados pelo chat. Não iniciar uma implementação genérica, mockada ou incompleta quando a ausência de um pré-requisito previsível impedir o desenvolvimento correto ou o teste real da TASK. Mocks continuam permitidos como complemento, nunca como substitutos da integração real exigida.

### 3. Implementação

Quando a validação estiver aprovada, implementar somente a TASK solicitada. Não implementar funcionalidades futuras, não alterar outras TASKs e manter os padrões arquiteturais definidos.

### 4. Testes

Após a implementação, executar todos os testes aplicáveis. Corrigir automaticamente as falhas encontradas dentro do escopo da TASK e repetir os testes até que sejam aprovados.

Testes unitários, mocks, análise estática, SQL offline e validadores de código não substituem testes reais de integração. Quando a TASK envolver banco de dados, contêiner, API, fila, serviço externo ou outra infraestrutura, preparar um ambiente real e isolado, baixar ou instalar as ferramentas necessárias e validar nele o fluxo implementado, incluindo os principais casos de sucesso, falha e reversão aplicáveis. Se uma validação real for tecnicamente impossível mesmo após esgotar as alternativas seguras, registrar exatamente o impedimento e nunca apresentar a TASK como plenamente validada.

Ambientes temporários de teste devem usar nomes, portas, credenciais e volumes isolados, sem alterar ou apagar dados reais do usuário, e devem ser encerrados e limpos após a validação. A validação real complementa, e não elimina, a execução da suíte automatizada.

### 5. Revisão técnica

Revisar integralmente a implementação antes de encerrar, verificando bugs, lógica, arquitetura, desempenho, segurança, organização, duplicação de código, aderência aos padrões e oportunidades de simplificação. Corrigir os problemas encontrados dentro do escopo da TASK antes de continuar.

### 6. Revisão da documentação

Confirmar que código e documentação permanecem sincronizados. Atualizar, sempre que aplicável, TODOS os documentos afetados pela conclusão da TASK, incluindo obrigatoriamente:

- `docs/PROJECT_CONTEXT.md`
- `docs/CHANGELOG.md`
- `docs/DECISION_LOG.md`
- `docs/ROADMAP.md`
- o status da TASK em `docs/tasks/TASK-XXX.md`
- `docs/tasks/README.md` (lista de TASKs concluídas e próxima TASK executável)
- este arquivo (`AGENTS.md`, seção "Estado atual")

Nenhuma TASK deve ser considerada encerrada enquanto houver documento desatualizado referenciando o status de TASKs. Isso é essencial para que qualquer agente de IA (Codex, Claude ou outro) retome o projeto de forma consistente em uma sessão futura, sem depender de memória de conversa anterior.

### 7. Controle de versão

Criar automaticamente um commit com Conventional Commits após a revisão final. Apresentar a mensagem do commit antes da confirmação final.

### 8. Repositório remoto

Após o commit final, publicar automaticamente a branch da TASK no repositório
remoto e atualizar a `main` local com essa branch. Não pedir confirmação para
essas duas operações. Nunca atualizar a `main` remota automaticamente: o push
de `main` para `origin/main` só pode ocorrer quando o usuário solicitar
explicitamente. Antes e depois das operações, verificar a sincronização e
informar separadamente os estados da branch da TASK, da `main` local e da
`main` remota.

### 9. Encerramento

Nunca iniciar automaticamente a próxima TASK. Encerrar apresentando resumo da implementação, arquivos criados e modificados, testes executados e seus resultados, problemas encontrados e correções realizadas, documentação atualizada, hash e mensagem do commit, status do repositório Git e status do repositório remoto.

## Estado atual

TASKs 000 a 054, TASK-055 a TASK-062, TASK-063 e TASK-064 concluídas — MVP
da V1 completo, checklist de release 65/65. A observabilidade da
TASK-045 (`DEC-031`) foi validada com PostgreSQL, API, worker, Collector,
Prometheus e Jaeger reais, incluindo falha/recuperação e privacidade. A
TASK-046 autentica a identidade mínima do canal Telegram (`DEC-032`). A
TASK-047 autoriza por papel único com `USER ⊂ ADMIN ⊂ DEV`, preservando
ownership inclusive para DEV (`DEC-034`). A TASK-061 adiciona senha e sessão
persistente (`DEC-035`). A TASK-048 protege secrets por arquivos, menor
privilégio e Gitleaks reproduzível (`DEC-036`). A TASK-049 adiciona limites,
replay/rate limit persistentes, retry seguro, circuit breakers locais e dead
letter append-only (`DEC-037`). A TASK-050 remove PII de logs, limita a
retenção da telemetria e desidentifica contas sem quebrar históricos
append-only (`DEC-038`). A TASK-051 documenta a operação do Ubuntu Server,
mantém endpoints administrativos privados e valida backup/restauração manual
sem confundir recuperação básica com disaster recovery (`DEC-039`). A TASK-052
mantém testes de integração reais, isolados e fail-closed em PostgreSQL 18.4,
obrigatórios no pipeline completo (`DEC-040`). A TASK-062 passou a orquestrar
automaticamente agenda, Store Providers, persistência, avaliação e publicação
de eventos (`DEC-041`). A TASK-053 refez o E2E reproduzível e o E2E externo depois da disponibilidade
por card, do DEC-045 (alertas por `amount`), do DEC-046 (intervalo/stagger) e
do DEC-047 (backoff persistente por fonte): ambos aprovados, com o E2E
externo classificado `PASS` em 2026-08-09 (quatro fontes reais, Pichau falhou
por instabilidade externa isolada sem virar `FAIL_INTERNO`, Telegram real
confirmou entrega sem duplicação). A TASK-053 está **concluída**, com
fechamento aprovado explicitamente pelo usuário; a condição externa da
Pichau fica registrada como observação de terceiro, não como bug interno
pendente. A TASK-054 (`docs/tasks/TASK-054.md`) fechou o checklist de
release (`docs/RELEASE_CHECKLIST.md`, 63/63) e publicou o tag Git anotado
`v1.0.0` em `origin`, por decisão explícita do usuário só como marco
revisado — sem deploy real num Ubuntu Server ainda, sem CI/CD e sem GitHub
Release pública. A TASK-063 (`docs/tasks/TASK-063.md`, `DEC-048`), aberta
depois de o usuário identificar no Telegram real que alertas podiam não
corresponder ao produto pedido, mostravam o nome da missão em vez do
anúncio real e sem link direto, está **concluída**: relevância
`MATCH`/`POSSIBLE_MATCH`/`NO_MATCH` (só `MATCH` alerta), correção do bug de
`previous` compartilhado entre missões, título/loja/link reais no alerta e
formatação revisada das mensagens principais, tudo validado (pipeline, E2E
reproduzível, missão real) e aprovado explicitamente. Durante essa
validação, a camada premium do `AdminDevAIProviderManager`
(`gemini-3.1-pro-preview`) mostrou 0% de sucesso sob carga real — isso foi
desmembrado para a TASK-064 (`docs/tasks/TASK-064.md`, `DEC-049`/`DEC-050`).
A TASK-064 está **concluída**, aprovada explicitamente pelo usuário em
2026-08-10: `AdminDevAIProviderManager` colapsado de 3 para 2 camadas
(Gemini Flash → Groq, mesmo modelo do perfil `USER`); nenhum nível
Pro/preview participa da cascata; `Settings.gemini_premium_model` removido.
Papel continua sendo só permissão/autorização, nunca escolha de modelo.
Validado com pipeline oficial (752 testes, 90,63% cobertura, 14
integrações reais), E2E reproduzível (2/2) e chamadas reais contra o stack
Docker reconstruído — fallback Flash→Groq real confirmado e uma coleta
representativa (missão descartável, uma fonte) obteve 15/20 sucesso em
classificação e em normalização; as falhas restantes do Flash por cota
ficam registradas como condição operacional externa, não como falha da
TASK-064. Batching não implementado (fora do escopo). Com a TASK-064
fechada, a condição que suspendia `v1.0.0` como release final ficou
resolvida quanto ao código (`docs/RELEASE_CHECKLIST.md`, 65/65) — mas
auditoria posterior (`DEC-051`) mostrou que a tag `v1.0.0` (`85b56c6`)
nunca foi movida e não continha TASK-063/TASK-064: o checklist "65/65"
descrevia o código corrente, não o que estava tagueado. `v1.0.0` permanece
**intocada** como marco histórico; a tag corretiva **`v1.0.1`** (`578dc29`)
passou a ser a referência de release. `docs/PRODUCTION_SETUP.md` documenta
a instalação completa em Ubuntu Server a partir dela, e a **`v1.0.1` já
foi implantada num servidor de produção real** — 7 serviços saudáveis,
migrations no head `20260809_0004`, webhook do Telegram ativo sobre HTTPS
pública, cadastro/login/missão validados ao vivo, proprietário promovido a
`DEV`. Achado real do deploy: os containers rodam como usuário não-root
(UID 999) e os arquivos de `.secrets/` precisaram ser `chown`ados pra esse
UID no servidor — bind mounts em host Linux real aplicam permissão POSIX
que o Docker Desktop no Windows não aplicava; corrigido só com permissão
de arquivo, nenhum código/compose alterado. Depois do deploy, o usuário
registrou seis novos itens de planejamento (nenhuma TASK criada, nenhum
código alterado), divididos em dois documentos separados para não
confundir as versões: **`docs/V1_0_2.md`** (release corretiva `v1.0.2`,
5 itens) — editar missão existente, categorias numeradas no `/cadastro` e
pré-lista de preços encontrados sem IA, um preço por loja
(`DEC-057`/`DEC-055`/`DEC-058`), além dos dois originais do `DEC-052`; e
**`docs/V1_2.md`** (evolução funcional V1.2, 12 itens) — redução de
`PriceObservation` redundante, Magalu como quinta loja, e comparação de
menor preço histórico externo/interno (estilo Steam Inventory Helper, a
mesma pré-lista acima com IA por cima) mais pesquisa de ofertas em lives
(YouTube/Shopee Live) (`DEC-053`/`DEC-054`/`DEC-056`). Ordem de versões
vigente: `v1.0.1` (atual, em produção) → `v1.0.2` (`docs/V1_0_2.md`,
corretiva) → V1.2 (`docs/V1_2.md`, funcional) → V2. Não iniciar qualquer
TASK de `v1.0.2`/V1.2/V2 sem pedido explícito.

**Atualização 2026-08-10:** por pedido explícito do usuário, a `v1.0.2`
entrou em **planejamento ativo**. Os 5 itens de `docs/V1_0_2.md` foram
convertidos em propostas de TASK (TASK-065 a TASK-069, uma por
responsabilidade, com ordem recomendada). `v1.0.1` em produção não foi
alterada; nenhuma tag `v1.0.2` foi criada; V1.2/V2 continuam sem qualquer
TASK.

A **TASK-065** (`docs/tasks/TASK-065.md`) está **concluída**: removeu
`AISHOPPING_GEMINI_MODEL`/`AISHOPPING_GROQ_MODEL` — confirmadas sem
propagação real em `compose.yaml` — de `.env.example` (raiz),
`backend/.env.example` e do `backend/.env` local, e corrigiu a
documentação (`docs/DEPENDENCIES.md`, `docs/PRODUCTION_SETUP.md`) que as
descrevia como configuráveis. `manager.py`, a cascata Flash→Groq e a
produção da `v1.0.1` não foram tocados.

A **TASK-066** (`docs/tasks/TASK-066.md`) está **concluída**: auditoria
encontrou a política parcial de restart já contraditória
(`collection_worker`/`telegram_notifier` com `unless-stopped` dependiam de
`database`, sem a política) e o usuário aprovou aplicar
`restart: unless-stopped` aos 7 serviços de `compose.yaml`. Validado com
pipeline oficial e teste real de crash simulado (recuperação automática
confirmada, ambiente local isolado, produção intocada).

A **TASK-067** (`docs/tasks/TASK-067.md`) está **concluída**: pesquisa ao
vivo da taxonomia real de Kabum, Pichau, Terabyte e Amazon.com.br,
consolidada em 15 categorias + "Todas" por critério objetivo (presente em
pelo menos 2 das 4 lojas), aprovada pelo usuário sem alterações. O
`/cadastro` passou a pedir `preferred_categories` pelo mesmo padrão
numerado de `favorite_stores`.

A **TASK-068** (`docs/tasks/TASK-068.md`) está **concluída**: pré-lista
informativa sem IA, disparada uma única vez por missão após a primeira
rodada completa de coleta. O usuário revisou o desenho inicial ("1 preço
por loja mostrando todas") durante a TASK e pediu mostrar as 2 ofertas
mais baratas entre as lojas que já responderam, mais uma única mensagem
de correção se uma loja mais lenta encontrar depois algo mais barato.
Reaproveita a classificação `MATCH` já calculada pela TASK-063 (nenhuma
IA nova); consumer Telegram dedicado (`telegram_prelist_v1`), sem afetar
alertas de queda/alvo (TASK-027/037). Validado com pipeline oficial e um
teste de integração real (PostgreSQL) cobrindo o cenário completo em 3
rodadas.

A **TASK-069** (`docs/tasks/TASK-069.md`) está **concluída**: novo
`IntentKind.EDIT_MISSION` edita lojas e/ou preço-alvo de uma missão já
criada, mas só enquanto `PAUSED`. Durante o desenho, o usuário pediu que
uma missão `ACTIVE` não seja rejeitada direto: o bot pergunta se quer
pausar agora (mesmo par confirmar/cancelar "1"/"2"), pausa se confirmado,
e orienta reenviar o pedido via novo comando `/editar-missao` — pausar e
editar nunca acontecem como um único passo automático. `MissionSchedule`
não é tocada (missão pausada nunca é reivindicada por
`claim_due_collections`) e o histórico (`CollectionRun`/
`PriceObservation`) de lojas removidas nunca é apagado. Nenhuma IA nova —
reusa `IntentInterpreter` e `interpret_confirmation_reply` já existentes.
Validado com pipeline oficial (815 testes, 90,69% cobertura) e testes de
integração real (PostgreSQL). Com esta TASK, os 5 itens do
**planejamento original** da `v1.0.2` estão implementados e validados;
nenhuma tag `v1.0.2` criada; produção da `v1.0.1` intocada.

Logo depois de aprovar a publicação da TASK-069, o usuário ampliou o
escopo da `v1.0.2` com mais dois itens (`DEC-060`, registrados em
`docs/V1_0_2.md` como itens 6 e 7): impedir `/cadastro` para usuário já
autenticado/logado; e perguntar as lojas por lista numerada
(`1 Pichau`/`2 Terabyte`/`3 Amazon`/`4 Kabum`/`5 Todas`) quando uma
missão for criada sem nenhuma informada. **Sem implementação e sem TASK
aberta** — pedido explícito de só registrar. **A `v1.0.2` continua
aberta.**

A **TASK-070** (`docs/tasks/TASK-070.md`, item 7) está **concluída**:
`CREATE_MISSION` sem loja nenhuma informada não assume mais as quatro
fontes da V1 automaticamente — encena um novo estado pendente
(`await_create_mission_sources`, preservando `search_query`/
`target_amount`/`target_currency` já interpretados) e pergunta por lista
numerada própria (ordem acima, diferente da usada pelo `/cadastro`,
TASK-067, que não foi alterado). A resposta é interpretada de forma
determinística, sem IA (`parse_numbered_store_selection`,
`backend/app/telegram/confirmation.py`), validando a entrada por
completo — qualquer token não reconhecido invalida a resposta inteira
(nunca aceita parcialmente), repetição é deduplicada, misturar "5" com
outro número ainda vira "todas". Só depois de uma seleção válida a
missão fica encenada e segue para a confirmação sim/não já existente
(TASK-058); a resposta numérica nunca passa pelo `IntentInterpreter` de
novo nem cria uma segunda missão.
`_DEFAULT_V1_SOURCE_CODES` (`backend/app/missions/service.py`)
permaneceu sem alteração — `backend/scripts/validate_collection_worker.py`
e um teste unitário ainda dependem dele; só o webhook deixou de
exercitá-lo. Validado com pipeline oficial completo; nenhuma tag
`v1.0.2` criada; produção da `v1.0.1` intocada. **O item 6 continua
registrado e pendente, sem TASK aberta — a `v1.0.2` continua aberta.**

A **TASK-071** (`docs/tasks/TASK-071.md`) está **concluída** — não é
item da `v1.0.2`, pedido explícito do usuário depois de uma simulação da
edição de missão (TASK-069) revelar um risco real: `sources` sempre foi
tratado como a lista completa final de lojas, mas a IA nunca sabe quais
lojas a missão já tem, então "adiciona kabum e terabyte" sem repetir a
loja já selecionada fazia a confirmação **remover** essa loja sem o
usuário perceber facilmente. `/editar-missao` virou um menu guiado,
**100% determinístico, sem `IntentInterpreter`**: resolve qual missão
(sem IA), depois `1 Lojas`/`2 Preço-alvo`, lojas ganha
`1 Adicionar`/`2 Remover` (nunca permite zerar todas), preço-alvo pede o
valor direto (`0` remove o alvo). **O caminho antigo por texto livre foi
desativado** por decisão explícita do usuário — `IntentKind.EDIT_MISSION`
continua existindo, mas o webhook só orienta a usar `/editar-missao`.
Reaproveita integralmente `edit_mission_criteria`,
`stage_edit_mission`/`describe_edit_mission`,
`stage_pause_for_edit`/`describe_pause_for_edit` (TASK-069) e
`parse_numbered_store_selection` (TASK-070) — nenhum alterado; a
confirmação final sim/não continua usando o classificador de IA já
existente (não é o `IntentInterpreter`). Validado com pipeline oficial
completo (876 testes, 91,00% cobertura, 21 integrações PostgreSQL
reais). Nenhuma tag `v1.0.2` criada; produção da `v1.0.1` intocada;
nenhuma outra TASK iniciada.

A **TASK-072** (`docs/tasks/TASK-072.md`, item 6, último item pendente
da `v1.0.2`) está **concluída**: `/cadastro` passa a ser bloqueado
quando `has_active_session` é `True` — mensagem fixa ("✅ Você já está
cadastrado e autenticado neste Telegram."), sem alterar
`registration_step` nem nenhum campo do perfil; sem sessão ativa, o
comportamento é idêntico ao de antes. Durante o desenho, o usuário
ampliou a preocupação para username duplicado entre contas e um
telefone com mais de uma conta — auditoria dedicada confirmou que essas
duas já eram estruturalmente garantidas (`User.telegram_user_id` e
`User.username` já têm constraint `UNIQUE` no banco;
`get_or_create_telegram_user` é seguro contra corrida; a sessão sempre é
resolvida pelo `telegram_user_id` recebido, nunca por dado informado
pelo usuário — nenhum caminho existe para autenticar na identidade de
outra pessoa), sem nenhuma mudança de código necessária. A lacuna real
era de UX: o passo `username` não consultava o banco antes de aceitar,
travando silenciosamente mais adiante quando duplicado — corrigido com
`_ensure_username_available` (`backend/app/users/registration.py`),
melhoria de UX que **não substitui** a constraint `UNIQUE`, que continua
sendo a proteção real contra corrida. Validado com pipeline oficial
completo (880 testes, 91,06% cobertura, 21 integrações PostgreSQL
reais). **Com esta TASK, os 7 itens da `v1.0.2` estão implementados e
validados — todo o escopo registrado desta versão está concluído.**
Nenhuma tag `v1.0.2` criada ainda; publicação final pendente de decisão
explícita do usuário; produção da `v1.0.1` intocada; nenhuma outra TASK
iniciada.

**Atualização 2026-08-15:** a continuidade passa a ocorrer diretamente no
Windows Server, no repositório autoritativo `C:\app\AIShoppingAgent`. O pacote
pontual de Telegram/autenticação foi commitado localmente em `fd68939`, sem
push, rebuild ou deploy; os containers permanecem na imagem anterior. O drift
preexistente do `alembic check` está aberto e documentado em
`docs/ALEMBIC_CHECK_ISSUE.md`. O handoff completo está em
`docs/HANDOFF_SERVER_2026-08-15.md`. Nenhuma nova TASK foi iniciada.

**Atualização 2026-08-15 (2):** `DEC-061` implementa roteamento exclusivamente
gratuito para USER e DEV: Gemini → Groq `openai/gpt-oss-120b` → OpenRouter
`openrouter/free`. Grounding DEV pesquisa pela Firecrawl API v2 direta e então
usa a mesma cascata gratuita de LLM; ADMIN compartilha a cascata
gratuita histórica, sem terceira política de IA. Sem rebuild, deploy ou push.
A TASK-086, "Corrigir drift de constraints no `alembic check`", foi registrada
e **não iniciada**.

**Atualização 2026-08-16:** a TASK-086 está concluída. A causa era o filtro do
Alembic 1.19.1 para checks `_type_bound` gerados por `Enum`; as três constraints
agora são explícitas na metadata, sem migration e sem alteração semântica. O
runner oficial aprovou `upgrade`, `downgrade -1`, novo upgrade, `alembic check`
e 29 integrações em PostgreSQL 18.4 descartável. Banco e containers ativos não
foram tocados. A TASK-076, pausada para remover esse bloqueio, deve ser retomada.

**Atualização 2026-08-16 (2):** a TASK-076 foi retomada e concluída.
`collection_source_failed` registra classe, detalhe seguro, status, etapa e
traceback local limitado; o `JsonFormatter` compartilhado permanece inalterado.
Testes focados e o runner oficial PostgreSQL 18.4 foram aprovados. Permanecem
pendentes TASK-077 e TASK-084.

**Atualização 2026-08-16 (3):** a TASK-087 está concluída. O catálogo aprovado
de textos visíveis foi aplicado ao Telegram, autenticação web, cadastro,
preferências, notificações e privacidade. Listas usam `1 — Opção` e confirmações
mostram as opções em linhas próprias; comandos, parsers, estados, TTLs e regras
funcionais permanecem inalterados. Foram aprovados 294 testes focados e 1.186
testes não-integração (1 ignorado, cobertura 90,68%). Permanecem pendentes
TASK-077 e TASK-084; nenhuma foi iniciada.

**Atualização 2026-08-16 (5):** a TASK-088 está concluída. O comando
`/listar_missoes`, seu alias com hífen e as entradas `missoes`/`missões`
listam, sem IA, até 15 missões recentes do proprietário nos estados ativa,
pausada e cancelada, em formato numerado, agrupando esses estados nessa ordem
e exibindo `🟢`/`⏸️`/`❌`. Concluídas/expiradas e missões de
outro usuário não aparecem. O menu nativo do Telegram inclui o novo comando.
Permanecem pendentes TASK-077 e TASK-084; nenhuma foi iniciada.

**Atualização 2026-08-16 (6):** a TASK-084 foi iniciada. O desenho aprovado
persiste imagem em `Offer`, usa `/r/{token}` próprio e checkpoint por
consumidor/evento/oferta/parte. A investigação real isolada confirmou imagens
nos cards de Pichau, Terabyte, Amazon e Kabum; a implementação está em curso.
TASK-077 permanece não iniciada.

**Atualização 2026-08-16 (7):** a TASK-084 está concluída. Imagens válidas são
persistidas por Offer sem apagamento por ausência posterior; `/r/{token}`
resolve destino exclusivamente pelo banco e valida o host da loja; o Telegram
envia uma oferta por parte com fallback textual de mídia; checkpoints incluem
consumidor, evento, oferta e parte. O runner oficial aprovou 32 integrações em
PostgreSQL 18.4 no head `20260816_0001`. Permanece pendente somente TASK-077.

**Atualização 2026-08-16 (4) — estado operacional autoritativo:** a `main` no
Windows Server está em `0e90cf0805a24cfd873d4d0257dacd8ae03c7920`, igual a
`origin/main`; os commits da TASK-086 (`f5c69e5`), TASK-076 (`c4b1e5e`),
IA/Firecrawl (`0513d0a`) e TASK-087 (`0e90cf0`) já existem no remoto e foram
implantados localmente. O stack de 7 serviços foi reconstruído e validado no
head Alembic `20260811_0001`, com `/health`, `/ready`, Funnel e webhook
saudáveis. O WSL2 está limitado por `%UserProfile%\.wslconfig` a 4 GB de RAM,
2 GB de swap e `autoMemoryReclaim=gradual`. Estado final das agendas: somente
as 4 missões `active` estão habilitadas; agendas de missões `cancelled`,
`completed` ou `expired` permanecem desabilitadas. Nenhum status de missão ou
`MissionTransition` foi alterado pela manutenção. Permanecem pendentes somente
TASK-077 e TASK-084; nenhuma foi iniciada.
