# Runbook de operação

Referência rápida para operar o AIShoppingAgent já em produção. Para a
instalação do zero, veja [Windows Server](../installation/windows-server.md).
Para procedimentos detalhados, esta página aponta para o documento
correto em vez de duplicar.

## Comandos rápidos

| Situação | Comando |
| --- | --- |
| Sistema está online? | `curl.exe --fail --silent --show-error http://127.0.0.1:8000/health` |
| Banco está acessível pela API? | `curl.exe --fail --silent --show-error http://127.0.0.1:8000/ready` |
| Status dos containers | `docker compose ps` |
| Logs recentes de um serviço | `docker compose logs --since=10m --tail=200 <serviço>` |
| Reiniciar um serviço | `docker compose restart <serviço>` |
| Subir tudo | `docker compose up -d` |
| Parar tudo (preserva volumes) | `docker compose stop` |
| Retomar depois de `stop` | `docker compose start` |
| Ver uso de disco do Docker | `docker system df` |
| Ver volumes | `docker volume ls` |

Detalhes de cada área:

- [Containers](containers.md) — listar, entrar, logs, restart, exec.
- [PostgreSQL](../database/postgresql.md) — acessar o banco.
- [Backup e restauração](backup-restore.md).
- [Atualização entre releases](../installation/update.md).
- [Health checks](health-checks.md).
- [Troubleshooting](../troubleshooting.md) — problemas comuns.

## Iniciar e parar

```powershell
docker compose up -d
docker compose ps
docker compose stop
docker compose start
```

`docker compose down` remove containers e rede, mas preserva volumes se não
receber `--volumes`. **Nunca** acrescente `--volumes`/`-v` em produção sem
uma decisão destrutiva explícita e um backup restaurado e testado em mãos —
isso apaga permanentemente o volume do PostgreSQL.

## Restart controlado

```powershell
docker compose restart api collection_worker telegram_notifier
docker compose ps
curl.exe --fail --silent --show-error http://127.0.0.1:8000/ready
```

## Diagnóstico rápido

| Sintoma | Verificações | Ação segura inicial |
| --- | --- | --- |
| `/health` falha | `docker compose ps api`, logs limitados | Reiniciar só a API e investigar crash/configuração. |
| `/health=200`, `/ready=503` | health do banco, espaço em disco, logs | Recuperar PostgreSQL; não culpar observabilidade. |
| Worker sem métricas | `docker compose ps collection_worker telegram_notifier`, logs | Reiniciar o worker afetado após encerrar transações e investigar secret/rede. |
| Telegram não entrega | `getWebhookInfo`, logs da API/worker, Tailscale Funnel | Validar Funnel e URL/secret sem imprimi-los; ver [Troubleshooting](../troubleshooting.md). |
| Disco pressionado | `docker system df`, volumes e filesystem | Não apagar volume; identificar imagens/logs seguros antes de qualquer limpeza. |

Mais cenários em [Troubleshooting](../troubleshooting.md).

## Rollback

### Rollback de código

Voltar para um commit/tag/container anterior só é permitido quando essa
aplicação é compatível com o schema já aplicado. Antes de trocar:

1. identifique as revisions Alembic conhecidas pelas duas versões;
2. revise alterações de colunas, constraints, enums e contratos;
3. confirme que a aplicação anterior aceita o schema atual;
4. mantenha o backup operacional validado ([Backup e
   restauração](backup-restore.md));
5. só então reconstrua e reinicie a versão anterior:

   ```powershell
   git checkout --detach <tag-anterior>
   docker compose build --pull
   docker compose up -d
   ```

Se a compatibilidade não puder ser demonstrada, pare. `git checkout`
seguido de restart não resolve rollback de forma geral e pode corromper
comportamento ou impedir a inicialização.

### Rollback de schema

Nunca execute downgrade Alembic destrutivo automaticamente. Cada downgrade
deve ser avaliado manualmente, principalmente quando remove ou transforma
dados, enums, constraints, FKs ou estruturas append-only. Se a aplicação
anterior não aceitar o schema atual, interrompa o procedimento e exija
intervenção técnica manual. Veja [Migrations](../database/migrations.md).

## Checklist rotineiro

### Diário

- containers esperados em execução/`healthy`;
- `/health` e `/ready` respondendo corretamente;
- worker expondo métricas internamente;
- ausência de dead letters/circuitos persistentemente abertos;
- espaço de disco e volume PostgreSQL sob controle;
- Tailscale Funnel de fato acessível de fora (não só "on" localmente).

### Antes de atualizar

- commit/tag atual registrado e checkout limpo;
- release candidata revisada;
- migrations avaliadas;
- backup restrito, não vazio e restaurado com sucesso em banco limpo;
- compatibilidade de rollback de código conhecida.

### Depois de atualizar

- revision Alembic esperada;
- API e workers saudáveis;
- PostgreSQL acessível;
- webhook/comandos do Telegram conferidos quando afetados;
- logs revisados sem dados sensíveis.
