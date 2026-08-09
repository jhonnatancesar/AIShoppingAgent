# Checklist de prontidão para produção

## Como usar este documento

Este é um **retrato do estado real do repositório**, não um plano. Ele
cruza os 8 critérios objetivos de conclusão do MVP (`docs/MVP.md`) e o
inventário completo das 62 tarefas planejadas (`docs/tasks/`) com o que
está de fato implementado hoje. `docs/MVP.md` já prevê "checklist de
release" como parte do critério 8 — este arquivo é esse checklist,
mantido como snapshot vivo enquanto o MVP avança, não o artefato final da
TASK-054 (preparar release), que ainda está pendente.

Atualizar este documento sempre que uma TASK relevante para produção for
concluída ou revisada. Snapshot gerado em **2026-08-08**, logo após a
conclusão da TASK-048.

## Resumo executivo

**Não está pronto para produção.** 56 das 62 tarefas planejadas estão
concluídas, mas as 6 pendentes cobrem áreas que separam
"funciona quando eu valido manualmente" de "está seguro para um usuário
real depender disso": privacidade, resiliência, documentação,
integração/release e — mais importante — não existe hoje nenhum
mecanismo que dispare a coleta de preços sozinho. Uma missão criada fica
"ativa" no banco, mas nada a pesquisa automaticamente.

## Critérios objetivos do MVP (`docs/MVP.md`) — status real

| # | Critério | Status | Evidência |
| --- | --- | --- | --- |
| 1 | Ambiente local sobe de forma documentada e reproduzível | ✅ Atendido | Docker Compose, `docs/DEPENDENCIES.md`, `scripts\check.cmd` |
| 2 | Usuário autorizado cria e consulta missão pelo Telegram | ✅ Atendido | TASK-046 autentica transporte/identidade; TASK-047 aplica RBAC/ownership; TASK-061 exige sessão por senha com Argon2id, TTL e recuperação segura |
| 3 | Sistema pesquisa todas as fontes selecionadas, normaliza e preserva histórico | ⚠️ Parcial | Store Providers (TASK-055) e normalização (TASK-025) existem e funcionam isoladamente, mas **nada os aciona automaticamente** — não existe worker/scheduler lendo `mission_schedules`, nem serviço que insira `PriceObservation` real em produção (confirmado durante a TASK-043) |
| 4 | Condição de preço produz evento e notificação rastreáveis | ⚠️ Parcial | Avaliação (TASK-027), publicação (TASK-043), consumo (TASK-044) e notificação Telegram (TASK-036) funcionam e foram validados realmente; ainda não existe fluxo de coleta que invoque automaticamente avaliação/publicação em produção |
| 5 | Recomendação/comparação básica com evidências históricas | ✅ Atendido | TASK-038 recomenda uma oferta com regras monetárias seguras e histórico identificável; TASK-039 compara as mesmas evidências, mantém a posição 1 invariável e não inventa total para frete desconhecido |
| 6 | Todo uso de IA passa pelo AI Provider Manager | ✅ Atendido | Invariante reforçada e validada em todas as tarefas de IA (TASK-028 a TASK-032, TASK-057 a TASK-060) |
| 7 | Fluxos críticos com testes de integração e ponta a ponta | ❌ Faltando | TASK-052 (integração) e TASK-053 (e2e) pendentes; validação real hoje é manual/pontual por TASK, sem suíte permanente |
| 8 | Documentação operacional, segurança mínima e checklist de release concluídos | ❌ Faltando | TASK-048 protegeu secrets; TASK-050 (privacidade), TASK-051 (documentação operacional) e TASK-054 (release) continuam pendentes |

## Bloqueios adicionais para rodar em produção de verdade

Além dos critérios formais do MVP:

- **Sem coleta automática**: embora a TASK-044 forneça o consumo genérico, não
  existe um orquestrador de `mission_schedules`; uma missão ativa não gera nenhuma
  observação de preço sozinha.
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
- **Sem limites nem resiliência** (TASK-049): sem rate limiting, retries
  padronizados ou circuit breakers além do que cada integração implementa
  isoladamente.
- **Notificador sem produtor automático**: a TASK-036 entrega eventos reais já
  publicados, mas hoje nenhum fluxo contínuo de coleta cria esses eventos sem
  preparação manual.
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
| Segurança e entrega — continuação | TASK-048 a TASK-054 | 1/7 | TASK-049 a TASK-054 |
| Store Providers e identidade | TASK-055, TASK-056 | 2/2 | — |
| Robustez, confirmação e IA (V1.2 antecipado) | TASK-057 a TASK-060 | 4/4 | — |

**Total: 56 concluídas, 6 pendentes.**

## Recomendação

Continuar validando manualmente por sessão (como já vem sendo feito) é
seguro. Colocar um usuário real dependendo do sistema hoje não é — os
maiores riscos são a ausência de coleta automática (a missão nunca
"funciona sozinha") e os controles restantes de segurança/release. A
ordem mais natural para fechar essas lacunas segue o próprio
`docs/ROADMAP.md`: TASK-049 e demais tarefas de
Segurança e entrega até a TASK-054.
