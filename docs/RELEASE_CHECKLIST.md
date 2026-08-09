# Checklist de prontidão para produção

## Como usar este documento

Este é um **retrato do estado real do repositório**, não um plano. Ele
cruza os 8 critérios objetivos de conclusão do MVP (`docs/MVP.md`) e o
inventário completo das 63 tarefas planejadas (`docs/tasks/`) com o que
está de fato implementado hoje. `docs/MVP.md` já prevê "checklist de
release" como parte do critério 8 — este arquivo é esse checklist,
mantido como snapshot vivo enquanto o MVP avança, não o artefato final da
TASK-054 (preparar release), que ainda está pendente.

Atualizar este documento sempre que uma TASK relevante para produção for
concluída ou revisada. Snapshot revisado em **2026-08-09**, após o preflight da
TASK-062 concluir a orquestração automática antes da TASK-053.

## Resumo executivo

**Não está pronto para produção.** 61 das 63 tarefas planejadas estão
concluídas, mas as 2 pendentes cobrem áreas que separam
"funciona quando eu valido manualmente" de "está seguro para um usuário
real depender disso": testes E2E externos e release. A coleta automática já
existe, mas ainda precisa ser comprovada ponta a ponta até a entrega ao usuário.

## Critérios objetivos do MVP (`docs/MVP.md`) — status real

| # | Critério | Status | Evidência |
| --- | --- | --- | --- |
| 1 | Ambiente local sobe de forma documentada e reproduzível | ✅ Atendido | Docker Compose, `docs/DEPENDENCIES.md`, `scripts\check.cmd` |
| 2 | Usuário autorizado cria e consulta missão pelo Telegram | ✅ Atendido | TASK-046 autentica transporte/identidade; TASK-047 aplica RBAC/ownership; TASK-061 exige sessão por senha com Argon2id, TTL e recuperação segura |
| 3 | Sistema pesquisa todas as fontes selecionadas, normaliza e preserva histórico | ✅ Atendido | TASK-062 agenda e executa automaticamente as fontes selecionadas, persiste observações append-only e isola falhas por loja |
| 4 | Condição de preço produz evento e notificação rastreáveis | ⚠️ Parcial | TASK-062 produz observações/eventos automaticamente e o notifier existe; a TASK-053 deve comprovar a cadeia externa até o usuário |
| 5 | Recomendação/comparação básica com evidências históricas | ✅ Atendido | TASK-038 recomenda uma oferta com regras monetárias seguras e histórico identificável; TASK-039 compara as mesmas evidências, mantém a posição 1 invariável e não inventa total para frete desconhecido |
| 6 | Todo uso de IA passa pelo AI Provider Manager | ✅ Atendido | Invariante reforçada e validada em todas as tarefas de IA (TASK-028 a TASK-032, TASK-057 a TASK-060) |
| 7 | Fluxos críticos com testes de integração e ponta a ponta | ⚠️ Parcial | TASK-052 mantém integração permanente em PostgreSQL real; TASK-053 (E2E externo) permanece pendente |
| 8 | Documentação operacional, segurança mínima e checklist de release concluídos | ⚠️ Parcial | TASK-051 entregou runbook, binds privados e restauração validada; o checklist/release final da TASK-054 permanece pendente |

## Bloqueios adicionais para rodar em produção de verdade

Além dos critérios formais do MVP:

- **Coleta automática implementada**: TASK-062 consome `mission_schedules`,
  executa as fontes selecionadas, persiste histórico e publica eventos. A
  TASK-053 ainda precisa validar externamente a entrega completa ao usuário.
- **Autenticação real aplicada**: TASK-061 usa Argon2id, tokens descartáveis,
  sessão absoluta de 12 horas e recuperação pelo Telegram vinculado. MFA,
  e-mail verificado e outro canal permanecem futuros.
- **Autorização aplicada** (TASK-047): RBAC usa `USER ⊂ ADMIN ⊂ DEV`, falha
  fechado e preserva ownership para todos os papéis; o proprietário foi
  promovido para DEV por operação one-shot auditada.
- **Secrets protegidos** (TASK-048): produção usa `/run/secrets` por serviço,
  execução non-root e Gitleaks fixado; rotação é manual e segura. Vault/cloud
  secret manager e rotação dinâmica não pertencem à V1.
- **Observabilidade disponível** (TASK-045): logs JSON, métricas Prometheus,
  traces Collector/Jaeger, health/readiness e regras de estado existem; ainda
  não há Alertmanager, que não pertenceu ao escopo aprovado.
- **Limites e resiliência aplicados** (TASK-049): corpo HTTP limitado, replay e
  rate limit persistentes, timeouts, retry somente seguro, circuit breakers por
  integração e dead letter append-only. Os circuitos continuam locais por
  processo, limitação deliberada da V1.
- **Privacidade técnica revisada** (TASK-050): PII saiu de logs/exceções,
  telemetria possui retenções limitadas, `/privacidade` não usa IA e existe
  desidentificação controlada que preserva a integridade append-only. O projeto
  não afirma anonimização irreversível nem certificação LGPD.
- **Runbook operacional disponível** (TASK-051): instalação e manutenção em
  Ubuntu Server, portas privadas, backup manual e restauração validada estão
  documentados. Isso não substitui disaster recovery nem resolve domínio/TLS,
  scheduler, CI/CD ou release.
- **Integração permanente disponível** (TASK-052): migrations, persistência,
  concorrência e fluxos críticos rodam em PostgreSQL 18.4 descartável e isolado
  como etapa obrigatória do pipeline; E2E externo continua na TASK-053.
- **Produtor e notifier desacoplados**: TASK-062 publica no event log e a
  TASK-036 consome os alertas; a prova E2E conjunta permanece na TASK-053.
- **Ambiente de validação é manual e efêmero**: a API roda no Docker Compose,
  mas o túnel `cloudflared` é recriado a cada sessão
  e o webhook do Telegram é reregistrado manualmente — não há deploy
  persistente, domínio fixo, TLS gerenciado nem CI/CD.

## Inventário de tarefas por fase

| Fase | Tarefas | Concluídas | Pendentes |
| --- | --- | --- | --- |
| Fundação e base técnica | TASK-000 a TASK-009 | 10/10 | — |
| Dados | TASK-010 a TASK-017 | 8/8 | — |
| Ciclo de vida de missões | TASK-018 | 1/1 | — |
| Missões e coleta | TASK-019 a TASK-026 | 8/8 | — |
| Alertas de preço | TASK-027 | 1/1 | — |
| Gerenciador de IA e Telegram | TASK-028 a TASK-037 | 10/10 | — |
| Compra e eventos | TASK-038 a TASK-041, TASK-043 a TASK-045 | 7/7 | — |
| Catálogo de eventos | TASK-042 | 1/1 | — |
| Segurança — canal, autorização e autenticação real | TASK-046, TASK-047, TASK-061 | 3/3 | — |
| Segurança e entrega — continuação | TASK-048 a TASK-054 | 5/7 | TASK-053 e TASK-054 |
| Orquestração automática do fluxo principal | TASK-062 | 1/1 | — |
| Store Providers e identidade | TASK-055, TASK-056 | 2/2 | — |
| Robustez, confirmação e IA (V1.2 antecipado) | TASK-057 a TASK-060 | 4/4 | — |

**Total: 61 concluídas, 2 pendentes.**

## Recomendação

Continuar validando manualmente por sessão (como já vem sendo feito) é
seguro. Colocar um usuário real dependendo do sistema hoje não é — os
maiores riscos são a ausência da prova E2E externa completa e da release. A
ordem mais natural para fechar essas lacunas segue o próprio
`docs/ROADMAP.md`: TASK-053 e TASK-054.
