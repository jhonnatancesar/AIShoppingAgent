# Tarefas

**TASK-088 concluída:** `/listar_missoes`, seus aliases e as palavras
`missoes`/`missões` listam deterministicamente missões ativas, pausadas e
canceladas; o menu nativo passa a publicar o novo comando.

Cada arquivo descreve uma unidade de trabalho. Antes de executar uma tarefa, leia os documentos obrigatórios definidos em `AGENTS.md`.

**TASK-076, TASK-086 e TASK-087 concluídas:** o drift das três constraints foi corrigido na metadata
sem editar migrations históricas; `alembic check` e o runner oficial foram
aprovados em PostgreSQL 18.4 descartável. A revisão transversal de copy foi
aplicada sem mudança funcional. Permanecem pendentes TASK-077 e TASK-084.

**Próximas TASKs pendentes:** TASK-077 e TASK-084. Nenhuma foi iniciada pela
conclusão da TASK-088.

**Deploy 2026-08-16:** TASK-076, TASK-086, o pacote de IA/Firecrawl e
TASK-087 estão presentes em `origin/main` e implantados no Windows Server no
HEAD `0e90cf0`. TASK-077 e TASK-084 continuam não iniciadas.

As TASKs 000 a 054, as TASKs 055 a 062, a TASK-063 e a TASK-064 estão
concluídas — MVP da V1 completo, checklist de release 65/65. A
TASK-038 recomenda
deterministicamente (`DEC-026`), a TASK-039 compara as mesmas evidências
(`DEC-027`), a TASK-040 define a confirmação com TTL (`DEC-028`) e a TASK-041
persiste sua solicitação imutável e resolução append-only (`DEC-029`). A
TASK-045 entrega observabilidade (`DEC-031`) e a TASK-046 fecha a autenticação
mínima do canal Telegram (`DEC-032`), e a TASK-047 aplica autorização por papel
único com ownership obrigatório (`DEC-034`). A TASK-061 concluiu autenticação
por senha e sessão persistente (`DEC-035`). A TASK-048 protege os secrets da V1
por arquivos e varredura reproduzível (`DEC-036`). A TASK-049 fecha limites,
replay, retries seguros, circuit breakers e dead letter (`DEC-037`). A TASK-050
fecha privacidade técnica com retenções limitadas e desidentificação fail-closed
(`DEC-038`). A TASK-051 consolida o runbook operacional, binds privados e
backup/restauração manual validada (`DEC-039`). A TASK-052 mantém a suíte
PostgreSQL real, isolada e obrigatória do pipeline (`DEC-040`). A TASK-062 fecha
a lacuna entre agenda, providers, histórico e event log (`DEC-041`). A
TASK-053 obteve `PASS` no E2E reproduzível (2/2) e no E2E externo com as
quatro fontes reais e Telegram real (2026-08-09), depois da disponibilidade
por card, do DEC-045, do DEC-046 e do DEC-047, e foi encerrada com aprovação
explícita do usuário. A falha isolada da Pichau no E2E externo é uma
condição externa observada, não um bug interno pendente. A TASK-054 fechou
`docs/RELEASE_CHECKLIST.md` (63/63) e publicou o tag `v1.0.0` em `origin`
como marco revisado da V1 — sem deploy real, CI/CD ou GitHub Release
pública, por decisão explícita do usuário. A TASK-063
(`docs/tasks/TASK-063.md`, `DEC-048`), aberta depois de o usuário
identificar no Telegram real alertas possivelmente irrelevantes ao produto
pedido, mostrando o nome da missão em vez do anúncio real e sem link
direto, está **concluída**: relevância, correção do bug de `previous`
compartilhado, título/loja/link reais e formatação das mensagens
principais, tudo validado e aprovado. A validação revelou a camada premium
da cascata ADMIN/DEV com 0% de sucesso sob carga — desmembrado para a
TASK-064 (`docs/tasks/TASK-064.md`, `DEC-049`/`DEC-050`), que está
**concluída**, aprovada explicitamente pelo usuário em 2026-08-10: cascata
`ADMIN`/`DEV` colapsada de 3 para 2 camadas (Gemini Flash → Groq, sem
nível Pro/preview), validada com pipeline oficial, E2E reproduzível e
chamadas reais (fallback Flash→Groq real confirmado; coleta representativa
com 15/20 sucesso em classificação e em normalização; falhas restantes do
Flash por cota registradas como condição operacional externa). Com a
TASK-064 fechada, a condição que suspendia a release como definitiva está
resolvida (`docs/RELEASE_CHECKLIST.md`, 65/65); o tag `v1.0.0` permanece
publicado sem alteração.

**Atualização 2026-08-10:** auditoria mostrou que `v1.0.0` nunca foi
movida e não contém TASK-063/TASK-064 — a tag corretiva **`v1.0.1`**
(`578dc29`) é a referência de release atual e **já foi implantada em
produção real** (`docs/PRODUCTION_SETUP.md`). O planejamento de `v1.0.2`
(release corretiva, 5 itens, `docs/V1_0_2.md`) e V1.2 (evolução
funcional, 12 itens, `docs/V1_2.md` — documento separado, não confundir
as duas versões) está registrado com `docs/DECISION_LOG.md` (`DEC-052` a
`DEC-059`).

**Atualização 2026-08-10 (2):** a `v1.0.2` entrou em **planejamento
ativo** por pedido explícito do usuário. Os 5 itens de `docs/V1_0_2.md`
foram propostos como TASK-065 a TASK-069 (numeração, nome, objetivo e
dependências apresentados ao usuário, ordem recomendada sugerida). V1.2
permanece só planejamento, sem nenhuma TASK.

**Atualização 2026-08-10 (3):** a **TASK-065** (`docs/tasks/TASK-065.md`)
foi aprovada, executada e está **concluída**: removeu
`AISHOPPING_GEMINI_MODEL`/`AISHOPPING_GROQ_MODEL` de `.env.example`
(raiz), `backend/.env.example` e do `backend/.env` local (limpeza não
versionada), sem alterar `manager.py`, a cascata Flash→Groq, `compose.yaml`
ou a produção da `v1.0.1`.

**Atualização 2026-08-10 (4):** a **TASK-066** (`docs/tasks/TASK-066.md`)
está **concluída**: auditoria encontrou que a política parcial de restart
já era contraditória (`collection_worker`/`telegram_notifier` com
`unless-stopped` dependiam de `database`, que não tinha); usuário aprovou
aplicar `restart: unless-stopped` aos 7 serviços; validado com pipeline
oficial e um teste real de crash simulado (`docker exec ... kill -9 1`)
confirmando recuperação automática.

**Atualização 2026-08-10 (5):** a **TASK-067** (`docs/tasks/TASK-067.md`)
está **concluída**: pesquisa ao vivo da taxonomia real de Kabum, Pichau,
Terabyte e Amazon.com.br, lista consolidada de 15 categorias + "Todas"
aprovada pelo usuário, `/cadastro` passou a usar o mesmo padrão numerado
de `favorite_stores`. `preferred_categories` segue sem consumidor além de
metadado.

**Atualização 2026-08-10 (6):** a **TASK-068** (`docs/tasks/TASK-068.md`)
está **concluída**: pré-lista informativa sem IA, disparada uma única
vez por missão após a primeira rodada completa de coleta, mostrando as
2 ofertas mais baratas entre as lojas que já responderam (escopo
revisado pelo usuário durante a TASK, diferente da proposta original de
"1 preço por loja mostrando todas"); uma única correção pode ser enviada
depois se uma loja mais lenta encontrar algo mais barato. Consumer
Telegram dedicado (`telegram_prelist_v1`), sem afetar alertas de
queda/alvo (TASK-027/037). Validado com pipeline oficial e um teste de
integração real (PostgreSQL) cobrindo as 3 rodadas do cenário completo.

**Atualização 2026-08-10 (7):** a **TASK-069** (`docs/tasks/TASK-069.md`)
está **concluída**: novo `IntentKind.EDIT_MISSION` permite editar lojas
e/ou preço-alvo de uma missão já criada, mas só enquanto `PAUSED`; uma
missão `ACTIVE` recebe, em vez disso, uma oferta de pausar primeiro
(confirmação "1"/"2", pedido durante o desenho), e o usuário reenvia a
edição via `/editar-missao` depois de pausada. `MissionSchedule` e o
histórico de coleta de lojas removidas nunca são tocados. Validado com
pipeline oficial (815 testes, 90,69% cobertura) e testes de integração
real (PostgreSQL), incluindo um cenário dedicado a provar que remover
uma loja não apaga seu `CollectionRun`. Com esta TASK, os 5 itens do
**planejamento original** da `v1.0.2` estão implementados e validados;
nenhuma tag `v1.0.2` criada.

**Atualização 2026-08-10 (8):** logo depois de aprovar a publicação da
TASK-069, o usuário ampliou o escopo da `v1.0.2` com mais dois itens
(`DEC-060`) — impedir `/cadastro` para usuário já autenticado/logado, e
perguntar as lojas por lista numerada (`1 Pichau`, `2 Terabyte`,
`3 Amazon`, `4 Kabum`, `5 Todas`) quando uma missão for criada sem
nenhuma informada. Registrados como itens 6 e 7 em `docs/V1_0_2.md`,
**sem implementação e sem TASK aberta** — pedido explícito de não
implementar agora. **A `v1.0.2` continua aberta.**

**Atualização 2026-08-11:** a **TASK-070** (`docs/tasks/TASK-070.md`,
item 7) está **concluída**: `CREATE_MISSION` sem loja nenhuma informada
não assume mais as quatro fontes da V1 automaticamente — encena um novo
estado pendente (`await_create_mission_sources`, preservando os demais
critérios já interpretados) e pergunta por lista numerada própria
(`1 Pichau/2 Terabyte/3 Amazon/4 Kabum/5 Todas`, ordem diferente da do
`/cadastro`, que não foi alterado). A resposta é interpretada de forma
determinística, sem IA, validando por completo — qualquer token não
reconhecido invalida a resposta inteira, repetição é deduplicada,
misturar "5" com outro número ainda vira "todas". Só depois de uma
seleção válida a missão segue para a confirmação sim/não já existente; o
fallback `_DEFAULT_V1_SOURCE_CODES` do service foi preservado sem
alteração, por depender de outros chamadores. Validado com pipeline
oficial completo. **O item 6 continua registrado e pendente, sem TASK
aberta — a `v1.0.2` continua aberta.**

**Atualização 2026-08-11 (2):** a **TASK-071**
(`docs/tasks/TASK-071.md`) está **concluída** — não é item da `v1.0.2`,
pedido explícito do usuário depois de uma simulação da edição de missão
(TASK-069) revelar um risco real: `sources` sempre foi tratado como a
lista completa final de lojas, mas a IA nunca sabe quais lojas a missão
já tem, então "adiciona kabum e terabyte" sem repetir a loja já
selecionada fazia a confirmação remover essa loja sem o usuário
perceber facilmente. `/editar-missao` virou um menu guiado, 100%
determinístico, sem `IntentInterpreter`: resolve qual missão (sem IA),
depois `1 Lojas`/`2 Preço-alvo`, lojas ganha `1 Adicionar`/`2 Remover`
(nunca permite zerar todas), preço-alvo pede o valor direto (`0` remove
o alvo). **O caminho antigo por texto livre foi desativado** por decisão
explícita do usuário. Reaproveita integralmente `edit_mission_criteria`,
`stage_edit_mission`/`describe_edit_mission`,
`stage_pause_for_edit`/`describe_pause_for_edit` (TASK-069) e
`parse_numbered_store_selection` (TASK-070) — nenhum alterado. Validado
com pipeline oficial completo (876 testes, 91,00% cobertura, 21
integrações PostgreSQL reais).

**Atualização 2026-08-11 (3):** a **TASK-072**
(`docs/tasks/TASK-072.md`, item 6) está **concluída**. `/cadastro` passa
a ser bloqueado quando a sessão já está ativa (`has_active_session`) —
mensagem fixa, sem alterar `registration_step` nem nenhum campo do
perfil; sem sessão ativa, o comportamento continua idêntico ao de hoje.
O usuário ampliou o pedido durante o desenho para incluir username
duplicado e um telefone com mais de uma conta; uma auditoria dedicada
mostrou que as duas últimas já eram estruturalmente garantidas
(constraints `UNIQUE` no banco + `get_or_create_telegram_user` seguro
contra corrida), sem nenhuma mudança necessária. A única lacuna real era
de UX: o passo `username` não consultava o banco antes de aceitar,
travando silenciosamente mais adiante quando duplicado — corrigido com
uma checagem antecipada (melhoria de UX, não substitui a constraint
`UNIQUE`, que continua ativa como proteção contra corrida). Validado com
pipeline oficial completo (880 testes, 91,06% cobertura, 21 integrações
PostgreSQL reais). **Com esta TASK, os 7 itens da `v1.0.2` estão
implementados e validados** — nenhuma tag `v1.0.2` criada ainda,
publicação final pendente de decisão explícita do usuário.
