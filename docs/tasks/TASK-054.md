# TASK-054 — Preparar release MVP

Status: Concluída em 2026-08-09 — tag `v1.0.0` criado e publicado em `origin`.
**Suspensa como release final entre 2026-08-09 e 2026-08-10**: primeiro a
TASK-063 (`DEC-048`) encontrou defeitos de relevância/apresentação nos
alertas, depois a validação real dessa correção revelou a TASK-064
(`DEC-049`/`DEC-050`, cascata ADMIN/DEV do `AIProviderManager` com 0% de
sucesso sob carga). O tag `v1.0.0` **não foi alterado nem recriado** em
nenhum momento. Com a TASK-063 e a TASK-064 concluídas e aprovadas
explicitamente pelo usuário, **a condição de suspensão está resolvida**
(`docs/releases/checklist.md`, 65/65, todos os 8 critérios do MVP
atendidos) — deploy real num Ubuntu Server continua fora do escopo já
aprovado desta TASK, exigindo decisão explícita futura.

**Nota posterior (2026-08-10):** a tag `v1.0.0` criada por esta TASK
(`85b56c6`) foi superada como referência corrente de release pela
**`v1.0.1`**, publicada depois das correções da TASK-063 e da TASK-064.
`v1.0.0` permanece intocada, sem ser recriada ou movida, como marco
histórico do estado da V1 em 2026-08-09 — não deve ser usada como
referência de deploy.

Dependência obrigatória: TASK-053 concluída (`PASS` no E2E reproduzível e
externo, 2026-08-09).

## Objetivo

Fechar o critério 8 do MVP (`docs/internal/mvp.md`): "documentação operacional, os
controles mínimos de segurança e o checklist de release estão concluídos".
Concretamente, finalizar `docs/releases/checklist.md` como o retrato real do
repositório e publicar um tag Git revisado marcando o estado pronto da V1,
para que `docs/operations/linux-runbook.md` possa usar "checkout somente do commit/tag
revisado" em vez de branch mutável.

## Escopo decidido com o usuário (2026-08-09)

- Só tag revisado — **sem deploy real** neste momento. Deploy num Ubuntu
  Server real fica para quando o servidor estiver provisionado, seguindo
  `docs/operations/linux-runbook.md` integralmente (fora desta TASK).
- Esquema de versão: `v1.0.0` (semver), primeira versão estável do MVP.
- Publicação: tag `v1.0.0` publicado em `origin`, **sem** GitHub Release
  pública.

CI/CD e deploy automático continuam fora do escopo da V1 (não constam em
`docs/internal/mvp.md`).

## Critério de aceite

- [x] `docs/releases/checklist.md` atualizado como retrato real (63/63 tarefas
  planejadas, 8/8 critérios objetivos do MVP atendidos);
- [x] pipeline oficial completo aprovado no commit taggeado;
- [x] tag Git anotado `v1.0.0` criado no commit revisado e publicado em
  `origin`;
- [x] documentação de controle sincronizada (`AGENTS.md`, `docs/internal/roadmap.md`,
  `docs/internal/project-context.md`, `docs/tasks/README.md`, `docs/releases/changelog.md`).

## Fora do escopo

- Deploy real num Ubuntu Server (`docs/operations/linux-runbook.md` continua sendo o
  runbook para quando isso for solicitado);
- CI/CD, pipeline de release automatizado ou publicação de imagem Docker;
- GitHub Release pública com notas;
- merge da branch da TASK em `main` (local ou remota) — não solicitado nesta
  TASK.
