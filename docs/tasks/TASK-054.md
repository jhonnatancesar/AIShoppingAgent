# TASK-054 — Preparar release MVP

Status: Concluída em 2026-08-09 — tag `v1.0.0` criado e publicado em `origin`.
**Suspensa como release final em 2026-08-09**: a TASK-063 (`DEC-048`)
encontrou defeitos de relevância/apresentação nos alertas antes da V1 ser
tratada como definitiva. O tag `v1.0.0` **não foi alterado nem recriado** e
continua publicado tal como está — só deixa de ser considerado o estado
final da V1 até a TASK-063 fechar.

Dependência obrigatória: TASK-053 concluída (`PASS` no E2E reproduzível e
externo, 2026-08-09).

## Objetivo

Fechar o critério 8 do MVP (`docs/MVP.md`): "documentação operacional, os
controles mínimos de segurança e o checklist de release estão concluídos".
Concretamente, finalizar `docs/RELEASE_CHECKLIST.md` como o retrato real do
repositório e publicar um tag Git revisado marcando o estado pronto da V1,
para que `docs/OPERATIONS.md` possa usar "checkout somente do commit/tag
revisado" em vez de branch mutável.

## Escopo decidido com o usuário (2026-08-09)

- Só tag revisado — **sem deploy real** neste momento. Deploy num Ubuntu
  Server real fica para quando o servidor estiver provisionado, seguindo
  `docs/OPERATIONS.md` integralmente (fora desta TASK).
- Esquema de versão: `v1.0.0` (semver), primeira versão estável do MVP.
- Publicação: tag `v1.0.0` publicado em `origin`, **sem** GitHub Release
  pública.

CI/CD e deploy automático continuam fora do escopo da V1 (não constam em
`docs/MVP.md`).

## Critério de aceite

- [x] `docs/RELEASE_CHECKLIST.md` atualizado como retrato real (63/63 tarefas
  planejadas, 8/8 critérios objetivos do MVP atendidos);
- [x] pipeline oficial completo aprovado no commit taggeado;
- [x] tag Git anotado `v1.0.0` criado no commit revisado e publicado em
  `origin`;
- [x] documentação de controle sincronizada (`AGENTS.md`, `docs/ROADMAP.md`,
  `docs/PROJECT_CONTEXT.md`, `docs/tasks/README.md`, `docs/CHANGELOG.md`).

## Fora do escopo

- Deploy real num Ubuntu Server (`docs/OPERATIONS.md` continua sendo o
  runbook para quando isso for solicitado);
- CI/CD, pipeline de release automatizado ou publicação de imagem Docker;
- GitHub Release pública com notas;
- merge da branch da TASK em `main` (local ou remota) — não solicitado nesta
  TASK.
