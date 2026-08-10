# Tarefas

Cada arquivo descreve uma unidade de trabalho. Antes de executar uma tarefa, leia os documentos obrigatórios definidos em `AGENTS.md`.

As TASKs 000 a 054, as TASKs 055 a 062 e a TASK-063 estão concluídas — MVP
da V1 completo. A TASK-064 tem implementação e validação real concluídas,
aguardando aprovação explícita do usuário para fechar. A
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
TASK-064 (`docs/tasks/TASK-064.md`, `DEC-049`/`DEC-050`). O usuário fechou
o escopo final (Flash único para USER/ADMIN/DEV, sem nível Pro/preview,
fallback só por disponibilidade) e autorizou a implementação, concluída em
2026-08-09/2026-08-10: cascata `ADMIN`/`DEV` colapsada de 3 para 2 camadas,
validada com pipeline oficial, E2E reproduzível e chamadas reais (fallback
Flash→Groq real confirmado; coleta pequena representativa com 75% de
sucesso na classificação). **Aguardando aprovação explícita do usuário
para fechar a TASK-064** — até lá, a release continua sem ser tratada como
definitiva; o tag `v1.0.0` permanece publicado sem alteração.
