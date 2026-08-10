# Tarefas

Cada arquivo descreve uma unidade de trabalho. Antes de executar uma tarefa, leia os documentos obrigatórios definidos em `AGENTS.md`.

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
dependências apresentados ao usuário, ordem recomendada sugerida),
aguardando aprovação explícita antes de qualquer arquivo `TASK-06X.md`
ser criado ou qualquer código alterado. V1.2 permanece só planejamento,
sem nenhuma TASK.
