# Checklist de prontidão para produção

## Como usar este documento

Este é um **retrato do estado real do repositório**, não um plano. Ele
cruza os 8 critérios objetivos de conclusão do MVP (`docs/MVP.md`) e o
inventário completo das 64 tarefas planejadas (`docs/tasks/`) com o que
está de fato implementado hoje. `docs/MVP.md` já prevê "checklist de
release" como parte do critério 8 — este arquivo é esse checklist, e a
TASK-054 o fechou publicando o tag `v1.0.0` sobre o commit revisado.

Atualizar este documento sempre que uma TASK relevante para produção for
concluída ou revisada. Snapshot revisado em **2026-08-09**, após a TASK-063
fechar e a TASK-064 ser registrada.

## Resumo executivo

**MVP funcionalmente completo, mas `v1.0.0` suspensa como release final
(2026-08-09).** A TASK-063 (`DEC-048`) fechou os defeitos reais de
relevância e apresentação nos alertas de preço encontrados antes deste
snapshot (nome da missão em vez do anúncio real, sem link direto, sem
filtro de correspondência produto-missão) — critério 4 abaixo voltou a
`✅ Atendido`. A validação real dessa correção revelou um problema
separado: a camada premium da cascata ADMIN/DEV do `AIProviderManager`
(`gemini-3.1-pro-preview`) teve 0% de sucesso sob carga real, o que pode
fazer o monitor perder classificações válidas por indisponibilidade de
IA, não por decisão de produto. Isso foi desmembrado para a TASK-064
(`DEC-049`), registrada e ainda pendente — por isso a release continua sem
ser tratada como definitiva. O tag `v1.0.0` continua publicado sem
alteração; deploy real num Ubuntu Server continua fora do escopo até
então.

## Critérios objetivos do MVP (`docs/MVP.md`) — status real

| # | Critério | Status | Evidência |
| --- | --- | --- | --- |
| 1 | Ambiente local sobe de forma documentada e reproduzível | ✅ Atendido | Docker Compose, `docs/DEPENDENCIES.md`, `scripts\check.cmd` |
| 2 | Usuário autorizado cria e consulta missão pelo Telegram | ✅ Atendido | TASK-046 autentica transporte/identidade; TASK-047 aplica RBAC/ownership; TASK-061 exige sessão por senha com Argon2id, TTL e recuperação segura |
| 3 | Sistema pesquisa todas as fontes selecionadas, normaliza e preserva histórico | ✅ Atendido | TASK-062 agenda e executa automaticamente as fontes selecionadas, persiste observações append-only e isola falhas por loja |
| 4 | Condição de preço produz evento e notificação rastreáveis | ✅ Atendido | TASK-063 (`DEC-048`) corrigiu o alerta para mostrar o anúncio real (título/loja/link), com filtro de relevância `(mission_id, offer_id)` — só `MATCH` alerta; validado com missão real no Telegram |
| 5 | Recomendação/comparação básica com evidências históricas | ✅ Atendido | TASK-038 recomenda uma oferta com regras monetárias seguras e histórico identificável; TASK-039 compara as mesmas evidências, mantém a posição 1 invariável e não inventa total para frete desconhecido |
| 6 | Todo uso de IA passa pelo AI Provider Manager | ⚠️ Parcial (reaberto) | Invariante arquitetural continua válida (TASK-028 a TASK-032, TASK-057 a TASK-060, TASK-063); TASK-064 (`DEC-049`) encontrou a camada premium da cascata ADMIN/DEV com 0% de sucesso sob carga real — disponibilidade prática da cascata, não violação da porta única, correção pendente de autorização |
| 7 | Fluxos críticos com testes de integração e ponta a ponta | ✅ Atendido | Integração, E2E reproduzível e E2E externo aprovados (`PASS`, 2026-08-09); quatro fontes reais, Telegram real, Pichau isolada por instabilidade externa sem contaminar as demais |
| 8 | Documentação operacional, segurança mínima e checklist de release concluídos | ✅ Atendido | TASK-051 entregou runbook, binds privados e restauração validada; TASK-054 fechou este checklist e publicou o tag `v1.0.0` |

## Bloqueios adicionais para rodar em produção de verdade

Além dos critérios formais do MVP:

- **Coleta automática implementada**: TASK-062 consome `mission_schedules`,
  executa as fontes selecionadas, persiste histórico e publica eventos. A
  TASK-053 validou externamente a entrega completa ao usuário (`PASS`).
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
  como etapa obrigatória do pipeline; E2E externo (TASK-053) aprovado.
- **Produtor e notifier desacoplados**: TASK-062 publica no event log e a
  TASK-036 consome os alertas; a prova E2E conjunta (TASK-053) aprovada.
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
| Segurança e entrega — continuação | TASK-048 a TASK-054 | 7/7 | — |
| Orquestração automática do fluxo principal | TASK-062 | 1/1 | — |
| Store Providers e identidade | TASK-055, TASK-056 | 2/2 | — |
| Robustez, confirmação e IA (V1.2 antecipado) | TASK-057 a TASK-060 | 4/4 | — |
| Relevância e apresentação de alertas | TASK-063 | 1/1 | — |
| Disponibilidade e fallback dos provedores de IA | TASK-064 | 0/1 | TASK-064 |

**Total: 64 concluídas, 1 pendente (TASK-064, registrada em 2026-08-09 antes
da release ser tratada como definitiva).**

## Recomendação

O MVP da V1 está funcionalmente completo e taggeado (`v1.0.0`); a TASK-063
fechou a rastreabilidade dos alertas (critério 4). A release **ainda não
deve ser tratada como definitiva** até a TASK-064 corrigir a disponibilidade
prática da cascata de IA (critério 6) — hoje a camada premium falha 100%
sob carga real, o que pode fazer o monitor perder classificações válidas
por indisponibilidade, não por decisão de produto. Continuar validando
manualmente por sessão (como já vem sendo feito) é seguro para uso próprio
sem deploy real. Colocar um usuário real dependendo do sistema em produção
ainda exige provisionar um Ubuntu Server real e executar
`docs/OPERATIONS.md` integralmente (domínio, TLS, deploy) —
deliberadamente fora do escopo da TASK-054 — **e** fechar a TASK-064
primeiro. Evoluções além disso ficam em `docs/V1_2.md` e
`docs/BACKLOG.md`, sob decisão explícita futura.
