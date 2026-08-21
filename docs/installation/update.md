# Atualização entre releases

Procedimento manual de atualização em produção. Nunca automatize esta
sequência e nunca use `git pull` cego em produção.

1. Registre o commit/tag atualmente em produção e identifique a
   release/tag candidata:

   ```powershell
   git rev-parse HEAD
   ```

2. Gere e valide um backup operacional **antes** de tocar em qualquer
   coisa — veja [Backup e restauração](../operations/backup-restore.md).

3. Busque as tags novas:

   ```powershell
   git fetch --tags --prune
   ```

4. Faça checkout da tag candidata e confirme o commit exato:

   ```powershell
   git checkout --detach <nova-tag>
   git status --short --branch
   git rev-parse HEAD
   git rev-list -n1 <nova-tag>
   ```

   `git status` deve mostrar a working tree limpa; as duas saídas do
   `rev-parse`/`rev-list` devem ser idênticas.

5. Leia as migrations novas entre a revisão atual e a candidata
   (`backend/migrations/versions/`, mensagens de cada revisão) — confira se
   alguma é destrutiva ou exige atenção manual. Veja
   [Migrations](../database/migrations.md).

6. Reconstrua as imagens:

   ```powershell
   docker compose config --quiet
   docker compose build --pull
   ```

7. Aplique as migrations:

   ```powershell
   docker compose run --rm api python -m alembic -c alembic.ini upgrade head
   ```

8. Recrie os serviços com a imagem nova:

   ```powershell
   docker compose up -d
   ```

9. Rode os health checks completos — veja
   [Windows Server](windows-server.md#7-verificação-dos-serviços).

10. Faça um smoke test sem carga alta (missão pequena, uma única fonte).

## Rollback

**Nunca faça downgrade destrutivo de banco como parte de uma atualização.**
Se a versão candidata não for compatível com o schema atual, pare e trate
como um rollback avaliado manualmente — não improvise. O procedimento
completo de rollback de código e de schema está no
[Runbook de operação](../operations/runbook.md#rollback).
